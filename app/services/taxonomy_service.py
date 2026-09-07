"""
Taxonomy service (spec #9): the admin-extendable controlled vocabulary
backing both the PubMed collector's default search vocabulary and the
rules stage's deterministic matching.

`seed_default_taxonomy` is idempotent and safe to call on every app/worker
startup -- it only inserts entries that don't already exist by
(category, term).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.taxonomy import DiscoveryTaxonomy

DEFAULT_TAXONOMY: list[dict] = [
    {"category": "disease_synonym", "term": "Charcot-Marie-Tooth", "synonyms": ["CMT"]},
    {"category": "disease_synonym", "term": "hereditary motor sensory neuropathy", "synonyms": ["HMSN"]},
    {"category": "disease_synonym", "term": "hereditary neuropathy", "synonyms": []},
    {"category": "gene", "term": "PMP22", "synonyms": []},
    {"category": "gene", "term": "MPZ", "synonyms": []},
    {"category": "gene", "term": "GJB1", "synonyms": []},
    {"category": "gene", "term": "MFN2", "synonyms": []},
    {"category": "gene", "term": "SH3TC2", "synonyms": []},
    {"category": "gene", "term": "GDAP1", "synonyms": []},
    {"category": "gene", "term": "LITAF", "synonyms": []},
    {"category": "gene", "term": "NEFL", "synonyms": []},
    {"category": "gene", "term": "FIG4", "synonyms": []},
    {"category": "gene", "term": "MORC2", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT1A", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT1B", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT2A", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT4C", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMTX1", "synonyms": []},
    {"category": "topic", "term": "physiotherapy", "synonyms": ["rehabilitation", "physical therapy"]},
    {"category": "topic", "term": "gene therapy", "synonyms": []},
    {"category": "topic", "term": "orthotics", "synonyms": ["AFO", "ankle foot orthosis"]},
    {"category": "topic", "term": "nutrition", "synonyms": ["diet"]},
]


def seed_default_taxonomy(db: Session) -> int:
    existing_keys = {
        (row.category, row.term)
        for row in db.execute(select(DiscoveryTaxonomy.category, DiscoveryTaxonomy.term)).all()
    }
    inserted = 0
    for item in DEFAULT_TAXONOMY:
        key = (item["category"], item["term"])
        if key in existing_keys:
            continue
        db.add(DiscoveryTaxonomy(category=item["category"], term=item["term"], synonyms=item["synonyms"]))
        inserted += 1
    if inserted:
        db.flush()
    return inserted
