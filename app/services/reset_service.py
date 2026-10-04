"""
Safe candidate-data reset (CMT-specific overhaul, Phase 1 of the reset
plan -- NOT executed by this pass; see IMPLEMENTATION_STATUS.md and the
Phase 0 audit for the confirmed FK-aware deletion order this implements).

CONFIRMED SCOPE (per explicit instruction): this reset CLEARS old
collection/runtime history, not just candidate-derived data --

    discovery_candidates, discovery_analysis, discovery_editorial_drafts,
    newsletter_items (for these candidates), rag_ingestion_requests,
    discovery_documents, discovery_source_records, discovery_runs,
    discovery_jobs

PRESERVED, never touched by this module:

    discovery_sources, discovery_taxonomy, discovery_audit_log,
    schema/migrations, auth/configuration

Deletion order respects every FK in the schema (children before parents):
newsletter_items -> rag_ingestion_requests -> discovery_editorial_drafts
-> discovery_analysis -> discovery_documents -> discovery_candidates, then
the source-side history (discovery_source_records -> discovery_runs),
then discovery_jobs (no FK to candidates, cleared independently so a
fresh run starts with an empty queue too).

`discovery_documents` (added per the "FINAL CORRECTIVE PROMPT" #12 --
"the safe reset must also correctly account for stored discovery
documents") has a NOT NULL foreign key to `discovery_candidates.id`, so
it must be deleted before candidates, same as analysis/editorial drafts.
Only the `discovery_documents` ROWS (metadata) are deleted here -- this
module has no filesystem access to the full-text storage volume and does
not attempt to delete the physical files on disk; those are orphaned by
a reset and would need a separate, explicit cleanup pass if disk space
recovery is ever required (out of scope here, since deleting files is a
different risk profile than deleting database rows and was not asked
for).

`discovery_audit_log.candidate_id` is NOT a foreign key (confirmed by
reading app/models/audit.py -- it's a plain indexed UUID column
specifically so audit history survives candidate deletion by design), so
it needs no special handling here and is never touched.

USAGE (all read/count operations are always safe to run; the actual
delete requires an explicit `confirm=True` and is never called by
anything in this codebase automatically):

    from app.database import session_scope
    from app.services.reset_service import dry_run_counts, execute_reset

    with session_scope() as db:
        print(dry_run_counts(db))          # safe, read-only, run this first

    with session_scope() as db:
        result = execute_reset(db, confirm=True)   # DESTRUCTIVE

A `pg_dump` backup is NOT something this module can take -- it has no
shell/OS access to the production database host from inside the
application process. See IMPLEMENTATION_STATUS.md for the exact backup
command to run manually, immediately before calling `execute_reset`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.analysis import DiscoveryAnalysis
from app.models.candidate import DiscoveryCandidate
from app.models.document import DiscoveryDocument
from app.models.editorial import DiscoveryEditorialDraft
from app.models.job import DiscoveryJob
from app.models.newsletter import NewsletterItem
from app.models.rag import RagIngestionRequest
from app.models.run import DiscoveryRun
from app.models.source_record import DiscoverySourceRecord

# Order matters -- children before parents, matching the FK graph exactly.
_TABLES_IN_DELETE_ORDER: list[tuple[str, type]] = [
    ("newsletter_items", NewsletterItem),
    ("rag_ingestion_requests", RagIngestionRequest),
    ("discovery_editorial_drafts", DiscoveryEditorialDraft),
    ("discovery_analysis", DiscoveryAnalysis),
    ("discovery_documents", DiscoveryDocument),
    ("discovery_candidates", DiscoveryCandidate),
    ("discovery_source_records", DiscoverySourceRecord),
    ("discovery_runs", DiscoveryRun),
    ("discovery_jobs", DiscoveryJob),
]

_PRESERVED_TABLES = [
    "discovery_sources", "discovery_taxonomy", "discovery_audit_log",
    "alembic_version (schema/migrations)", "auth/configuration (not a table -- env vars)",
]


@dataclass
class ResetCounts:
    counts: dict[str, int] = field(default_factory=dict)
    preserved: list[str] = field(default_factory=lambda: list(_PRESERVED_TABLES))

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def dry_run_counts(db: Session) -> ResetCounts:
    """Read-only. Always safe to call. Use this to see exactly what a
    reset would delete before ever calling execute_reset."""
    counts: dict[str, int] = {}
    for table_name, model in _TABLES_IN_DELETE_ORDER:
        counts[table_name] = db.execute(select(func.count()).select_from(model)).scalar_one()
    return ResetCounts(counts=counts)


@dataclass
class ResetResult:
    pre_counts: ResetCounts
    post_counts: ResetCounts
    verified_clean: bool


def execute_reset(db: Session, *, confirm: bool) -> ResetResult:
    """
    DESTRUCTIVE. Deletes every row in every table listed in
    _TABLES_IN_DELETE_ORDER. Never call this without:

      1. A verified pg_dump backup already taken (this function cannot
         take one itself -- no OS/shell access from the application
         process to the database host).
      2. Having already reviewed dry_run_counts(db) output.
      3. Explicit human sign-off for this specific run.

    Runs as a single transaction: the caller's session_scope() (or
    equivalent) commits only if this function returns normally, and
    rolls back entirely if anything raises -- so a failure partway
    through never leaves the database in a half-reset state.

    Deliberately requires `confirm=True` as a keyword-only argument (not
    a default) so it can never be invoked accidentally by a bare
    `execute_reset(db)` call.
    """
    if not confirm:
        raise ValueError(
            "execute_reset() called without confirm=True -- refusing to run. "
            "Call dry_run_counts(db) first, review it, take a pg_dump backup, "
            "then call execute_reset(db, confirm=True) explicitly."
        )

    pre_counts = dry_run_counts(db)

    for table_name, model in _TABLES_IN_DELETE_ORDER:
        db.execute(delete(model))
    db.flush()

    post_counts = dry_run_counts(db)
    verified_clean = post_counts.total == 0

    return ResetResult(pre_counts=pre_counts, post_counts=post_counts, verified_clean=verified_clean)


# ===========================================================================
# Selective reset: "clean the candidate dataset but keep ONE candidate" --
# a deliberately narrower sibling of execute_reset() above, added for the
# "Simplify Discovery Newsletter Workflow and Clean Dataset" task.
#
# Scope is intentionally NOT the same as execute_reset()'s full wipe:
#
#   DELETED, for every candidate EXCEPT the retained one:
#       newsletter_items, rag_ingestion_requests, discovery_editorial_drafts,
#       discovery_analysis, discovery_documents, discovery_source_records,
#       discovery_candidates
#
#   NEVER TOUCHED (deliberately out of scope for a "clean the CANDIDATE
#   dataset" operation, unlike the full reset above):
#       discovery_sources      -- source/collector CONFIGURATION, not
#                                  candidate data; removing a source
#                                  definition was never asked for.
#       discovery_runs         -- collection RUN history, tied to a
#                                  source, not a candidate; multiple
#                                  candidates/source_records can derive
#                                  from the same run, so it is not
#                                  "the candidate's" to delete.
#       discovery_jobs         -- worker queue history; has no
#                                  candidate_id column at all (confirmed
#                                  by reading app/models/job.py).
#       discovery_audit_log    -- same reasoning as execute_reset(): no
#                                  FK to candidates (by design, see
#                                  app/models/audit.py), and an audit
#                                  trail is meant to survive the data it
#                                  describes being removed.
#       discovery_taxonomy     -- admin-configured vocabulary, unrelated
#                                  to any specific candidate.
#       newsletter_publications -- a publication record is real
#                                  newsletter-issue history; it is not
#                                  itself tied to one candidate_id (its
#                                  `item_ids` is a plain JSON list with no
#                                  DB-level FK -- confirmed by reading
#                                  app/models/newsletter.py), and deleting
#                                  newsletter_items for removed candidates
#                                  cannot violate any constraint on it.
#
# `discovery_source_records.candidate_id` is NULLABLE (a record that was
# collected but never promoted to a candidate during dedup/validation) --
# those orphaned rows are deleted too (candidate_id IS NULL), since they
# are still part of "the current candidate dataset" being cleaned and
# cannot belong to the retained candidate.
# ===========================================================================

_SELECTIVE_TABLES_IN_DELETE_ORDER: list[tuple[str, type]] = [
    ("newsletter_items", NewsletterItem),
    ("rag_ingestion_requests", RagIngestionRequest),
    ("discovery_editorial_drafts", DiscoveryEditorialDraft),
    ("discovery_analysis", DiscoveryAnalysis),
    ("discovery_documents", DiscoveryDocument),
    ("discovery_source_records", DiscoverySourceRecord),
    ("discovery_candidates", DiscoveryCandidate),
]

_SELECTIVE_PRESERVED_TABLES = [
    "discovery_sources", "discovery_runs", "discovery_jobs", "discovery_taxonomy",
    "discovery_audit_log", "newsletter_publications",
    "alembic_version (schema/migrations)", "auth/configuration (not a table -- env vars)",
]


def _normalize_candidate_id(retain_candidate_id: uuid.UUID | str) -> uuid.UUID:
    return retain_candidate_id if isinstance(retain_candidate_id, uuid.UUID) else uuid.UUID(str(retain_candidate_id))


@dataclass
class SelectiveResetCounts:
    """`deleted` is "how many rows in this table do NOT belong to the
    retained candidate" (i.e. what a real run would remove); `retained`
    is "how many rows belong to the retained candidate" (what survives).
    Both are always safe, read-only counts."""

    retain_candidate_id: uuid.UUID
    retained_candidate_exists: bool
    deleted: dict[str, int] = field(default_factory=dict)
    retained: dict[str, int] = field(default_factory=dict)
    preserved: list[str] = field(default_factory=lambda: list(_SELECTIVE_PRESERVED_TABLES))

    @property
    def total_to_delete(self) -> int:
        return sum(self.deleted.values())


def dry_run_selective_counts(db: Session, retain_candidate_id: uuid.UUID | str) -> SelectiveResetCounts:
    """Read-only. Always safe to call. Shows exactly what
    execute_selective_reset() would delete vs. retain, without changing
    anything -- run this first, same discipline as dry_run_counts()."""
    retain_id = _normalize_candidate_id(retain_candidate_id)
    retained_candidate_exists = db.get(DiscoveryCandidate, retain_id) is not None

    deleted: dict[str, int] = {}
    retained: dict[str, int] = {}
    for table_name, model in _SELECTIVE_TABLES_IN_DELETE_ORDER:
        if model is DiscoveryCandidate:
            deleted[table_name] = db.execute(
                select(func.count()).select_from(model).where(model.id != retain_id)
            ).scalar_one()
            retained[table_name] = db.execute(
                select(func.count()).select_from(model).where(model.id == retain_id)
            ).scalar_one()
        elif model is DiscoverySourceRecord:
            # Nullable FK: "not the retained candidate" includes rows
            # that never became any candidate at all.
            deleted[table_name] = db.execute(
                select(func.count()).select_from(model).where(
                    (model.candidate_id.is_(None)) | (model.candidate_id != retain_id)
                )
            ).scalar_one()
            retained[table_name] = db.execute(
                select(func.count()).select_from(model).where(model.candidate_id == retain_id)
            ).scalar_one()
        else:
            deleted[table_name] = db.execute(
                select(func.count()).select_from(model).where(model.candidate_id != retain_id)
            ).scalar_one()
            retained[table_name] = db.execute(
                select(func.count()).select_from(model).where(model.candidate_id == retain_id)
            ).scalar_one()

    return SelectiveResetCounts(
        retain_candidate_id=retain_id, retained_candidate_exists=retained_candidate_exists,
        deleted=deleted, retained=retained,
    )


@dataclass
class SelectiveResetResult:
    pre_counts: SelectiveResetCounts
    post_counts: SelectiveResetCounts
    verified_clean: bool  # True iff exactly the retained candidate (and only its own rows) remain


def execute_selective_reset(db: Session, *, retain_candidate_id: uuid.UUID | str, confirm: bool) -> SelectiveResetResult:
    """
    DESTRUCTIVE. Deletes every row in every table listed in
    _SELECTIVE_TABLES_IN_DELETE_ORDER that does NOT belong to
    `retain_candidate_id`, leaving that one candidate (and only its own
    dependent rows) in place. Never call this without:

      1. A verified backup already taken (same standing requirement as
         execute_reset() -- this function has no OS/shell access to the
         database host and cannot take one itself).
      2. Having already reviewed dry_run_selective_counts(db, ...).
      3. Explicit human sign-off for this specific run.

    Refuses to run (raises ValueError, touches nothing) if:
      - confirm is not True.
      - `retain_candidate_id` does not exist as a DiscoveryCandidate --
        this is a deliberate safety rail: without it, a mistyped/stale
        UUID would silently fall through to "nothing matches, so keep
        nothing", deleting every candidate instead of all-but-one.

    Runs as a single transaction, same as execute_reset(): the caller's
    session_scope() (or equivalent) commits only if this function returns
    normally, and rolls back entirely if anything raises.
    """
    if not confirm:
        raise ValueError(
            "execute_selective_reset() called without confirm=True -- refusing to run. "
            "Call dry_run_selective_counts(db, retain_candidate_id) first, review it, "
            "take a backup, then call execute_selective_reset(db, retain_candidate_id=..., confirm=True) explicitly."
        )

    retain_id = _normalize_candidate_id(retain_candidate_id)
    if db.get(DiscoveryCandidate, retain_id) is None:
        raise ValueError(
            f"Refusing to run: no DiscoveryCandidate with id={retain_id} exists. "
            "execute_selective_reset() requires the candidate to be retained to already exist -- "
            "otherwise every candidate would be deleted with none retained."
        )

    pre_counts = dry_run_selective_counts(db, retain_id)

    for table_name, model in _SELECTIVE_TABLES_IN_DELETE_ORDER:
        if model is DiscoveryCandidate:
            db.execute(delete(model).where(model.id != retain_id))
        elif model is DiscoverySourceRecord:
            db.execute(delete(model).where((model.candidate_id.is_(None)) | (model.candidate_id != retain_id)))
        else:
            db.execute(delete(model).where(model.candidate_id != retain_id))
    db.flush()

    post_counts = dry_run_selective_counts(db, retain_id)
    verified_clean = (
        post_counts.total_to_delete == 0
        and post_counts.retained_candidate_exists
        and post_counts.retained.get("discovery_candidates") == 1
    )

    return SelectiveResetResult(pre_counts=pre_counts, post_counts=post_counts, verified_clean=verified_clean)
