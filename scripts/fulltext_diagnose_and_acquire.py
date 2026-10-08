#!/usr/bin/env python3
"""
Full-text acquisition: diagnose why no candidate has full text, and (only
when asked) acquire genuine original full text for a few eligible ones.

Thin wrapper over the EXISTING pipeline -- it calls
app.services.fulltext.resolver.resolve_full_text and
app.services.fulltext.service.acquire_full_text exactly as the worker's
`resolve_full_text` job does. It changes no pipeline logic, never writes
text itself, and never sets `full_text_available` directly: that flag only
becomes True inside acquire_full_text, after real bytes were downloaded
from Europe PMC and recognised as pdf/xml/html.

Run from the repo root with the SAME environment the API/worker use
(DATABASE_URL, FULLTEXT_STORAGE_DIR, ...), on the VPS (it needs outbound
access to ebi.ac.uk).

    # 1. Read-only diagnosis (default). Writes nothing.
    python3 scripts/fulltext_diagnose_and_acquire.py

    # 2. Read-only probe of more candidates (resolver only, no downloads).
    python3 scripts/fulltext_diagnose_and_acquire.py --probe 40

    # 3. Acquire for up to N eligible candidates (downloads + stores).
    python3 scripts/fulltext_diagnose_and_acquire.py --acquire 3

    # 4. Acquire for specific candidates.
    python3 scripts/fulltext_diagnose_and_acquire.py --acquire 1 --candidate-id <uuid>

A candidate is reported "GENUINE" only if its document row has
retrieval_status == "acquired", a real full_text_format (pdf/xml/html), a
document_url on europepmc.org / ebi.ac.uk, a stored file (document_ref +
content_hash) and extracted text of at least --min-chars characters (an
abstract is ~1-2k characters; real articles are tens of thousands).
Exit code 0 if at least one genuine candidate exists after the run, else 1.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.database import session_scope  # noqa: E402
from app.models.candidate import DiscoveryCandidate  # noqa: E402
from app.models.document import DiscoveryDocument  # noqa: E402
from app.services.fulltext.resolver import resolve_full_text  # noqa: E402
from app.services.fulltext.service import acquire_full_text  # noqa: E402

GENUINE_HOSTS = ("europepmc.org", "ebi.ac.uk")


def _is_genuine(doc: DiscoveryDocument | None, min_chars: int) -> tuple[bool, str]:
    if doc is None:
        return False, "no document row"
    if doc.retrieval_status != "acquired":
        return False, f"retrieval_status={doc.retrieval_status}"
    if doc.full_text_format not in ("pdf", "xml", "html"):
        return False, f"full_text_format={doc.full_text_format}"
    if not doc.document_url or not any(h in doc.document_url for h in GENUINE_HOSTS):
        return False, f"document_url not an Europe PMC/EBI source: {doc.document_url}"
    if not (doc.document_ref and doc.content_hash):
        return False, "file not stored (no document_ref/content_hash)"
    chars = doc.extracted_char_count or 0
    if doc.extraction_status != "success" or chars < min_chars:
        return False, f"extraction_status={doc.extraction_status}, extracted_chars={chars} (< {min_chars})"
    return True, f"{doc.full_text_format}, {chars} chars, {doc.document_url}"


def _diagnose(db) -> None:
    total = db.execute(select(func.count()).select_from(DiscoveryCandidate)).scalar_one()
    by_type = db.execute(
        select(DiscoveryCandidate.content_type, func.count()).group_by(DiscoveryCandidate.content_type)
    ).all()
    with_ids = db.execute(
        select(func.count()).select_from(DiscoveryCandidate).where(
            DiscoveryCandidate.content_type == "research_paper",
            (DiscoveryCandidate.pmid.is_not(None)) | (DiscoveryCandidate.doi.is_not(None)),
        )
    ).scalar_one()
    print(f"candidates total: {total}")
    print(f"  by content_type: {dict(by_type)}")
    print(f"  research_paper with a PMID or DOI (resolvable): {with_ids}")
    print(f"  full_text_available=True: "
          f"{db.execute(select(func.count()).select_from(DiscoveryCandidate).where(DiscoveryCandidate.full_text_available.is_(True))).scalar_one()}")

    rows = db.execute(
        select(DiscoveryDocument.retrieval_status, func.count()).group_by(DiscoveryDocument.retrieval_status)
    ).all()
    print(f"discovery_documents rows by retrieval_status: {dict(rows) or '{} (NO acquisition attempt was ever recorded)'}")

    never_tried = db.execute(
        select(func.count()).select_from(DiscoveryCandidate).where(
            DiscoveryCandidate.content_type == "research_paper",
            ~select(DiscoveryDocument.id).where(DiscoveryDocument.candidate_id == DiscoveryCandidate.id).exists(),
        )
    ).scalar_one()
    print(f"research_paper candidates with NO document row (acquisition never attempted): {never_tried}")

    reasons = Counter(
        (d.error_detail or "")[:90]
        for d in db.execute(
            select(DiscoveryDocument).where(DiscoveryDocument.retrieval_status != "acquired")
        ).scalars()
    )
    if reasons:
        print("top reasons recorded on non-acquired attempts:")
        for reason, n in reasons.most_common(8):
            print(f"  {n:>4}  {reason}")


def _eligible(db, limit: int, only: list[uuid.UUID] | None) -> list[DiscoveryCandidate]:
    q = select(DiscoveryCandidate).where(
        DiscoveryCandidate.content_type == "research_paper",
        DiscoveryCandidate.full_text_available.is_(False),
        (DiscoveryCandidate.pmid.is_not(None)) | (DiscoveryCandidate.doi.is_not(None)),
    )
    if only:
        q = q.where(DiscoveryCandidate.id.in_(only))
    # Newest first: recent papers are the most likely to carry a PMCID
    # in Europe PMC's open-access subset.
    q = q.order_by(DiscoveryCandidate.created_at.desc()).limit(limit)
    return list(db.execute(q).scalars())


async def _probe(candidates) -> list[DiscoveryCandidate]:
    ok = []
    for c in candidates:
        res = await resolve_full_text(c)
        chain = ",".join(x.full_text_format for x in res.candidates) or "-"
        print(f"  {str(c.id)[:8]} pmid={c.pmid} doi={c.doi}  open_access_resolvable={res.available}  chain={chain}"
              + ("" if res.available else f"  ({res.reason})"))
        if res.available:
            ok.append(c)
    return ok


async def _acquire(db, candidates, n: int, min_chars: int) -> int:
    genuine = 0
    for c in candidates:
        if genuine >= n:
            break
        doc = await acquire_full_text(db, c)
        db.commit()
        good, why = _is_genuine(doc, min_chars)
        print(f"  {str(c.id)[:8]} -> {'GENUINE' if good else 'not usable'}: {why}")
        genuine += 1 if good else 0
    return genuine


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", type=int, default=15, help="how many eligible candidates to resolver-probe (read-only)")
    ap.add_argument("--acquire", type=int, default=0, help="acquire full text for up to N genuine candidates (writes)")
    ap.add_argument("--candidate-id", action="append", default=[], help="restrict to these candidate ids")
    ap.add_argument("--min-chars", type=int, default=5000, help="min extracted chars to count as genuine full text")
    args = ap.parse_args()
    only = [uuid.UUID(x) for x in args.candidate_id] or None

    with session_scope() as db:
        print("== diagnosis ==")
        _diagnose(db)

        pool = max(args.probe, args.acquire * 8)
        cands = _eligible(db, pool, only)
        print(f"\n== resolver probe (read-only) over {len(cands)} eligible candidates ==")
        resolvable = asyncio.run(_probe(cands))
        print(f"resolvable via Europe PMC open access: {len(resolvable)} of {len(cands)}")

        if args.acquire:
            print(f"\n== acquiring (up to {args.acquire}) ==")
            asyncio.run(_acquire(db, resolvable, args.acquire, args.min_chars))

        print("\n== genuine full-text candidates currently in the database ==")
        docs = db.execute(
            select(DiscoveryDocument).where(DiscoveryDocument.retrieval_status == "acquired")
        ).scalars().all()
        n_good = 0
        for d in docs:
            good, why = _is_genuine(d, args.min_chars)
            if good:
                n_good += 1
                cand = db.get(DiscoveryCandidate, d.candidate_id)
                print(f"  candidate_id={d.candidate_id} full_text_available={cand.full_text_available}  {why}")
                print(f"    title: {cand.title}")
                print(f"    GET /api/v1/candidates/{d.candidate_id}/document")
        print(f"genuine: {n_good}")
        return 0 if n_good else 1


if __name__ == "__main__":
    raise SystemExit(main())
