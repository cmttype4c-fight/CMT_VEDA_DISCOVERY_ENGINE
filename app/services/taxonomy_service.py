"""
Taxonomy service (spec #9): the admin-extendable controlled vocabulary
backing both the PubMed collector's default search vocabulary and the
rules stage's deterministic matching.

`seed_default_taxonomy` is idempotent and safe to call on every app/worker
startup -- it only inserts entries that don't already exist by
(category, term). It is now actually wired into startup (see
app/main.py's startup event and app/worker/worker.py's run_forever) --
previously it existed but was only ever called from tests, which is why
production's discovery_taxonomy table was empty. See
IMPLEMENTATION_STATUS.md, "CMT-specific overhaul, Phase 1" for the full
root-cause note.

CMT-SPECIFIC OVERHAUL, PHASE 1 (taxonomy expansion):
The subtype/topic list below is expanded from the original 5-subtype /
4-topic seed to the categories your spec listed. Disease terminology and
CMT subtype names are standard nomenclature (the classic CMT1/CMT2/CMT3
(Dejerine-Sottas)/CMT4/CMTX/CMT-DI numbering scheme used throughout the
CMT clinical/research literature, e.g. GeneReviews' "Charcot-Marie-Tooth
Hereditary Neuropathy Overview"). The gene list is similarly restricted to
genes that are widely and consistently cited as CMT-associated across
that literature -- per your explicit instruction not to invent a gene
list, this is NOT a novel or exhaustive clinical-genetics list, and
should be reviewed by someone with clinical/genetics authority before
being treated as complete or authoritative. Provenance: compiled from
terms that already appeared in this codebase's own seed/docstrings
(PMP22, MPZ, GJB1, MFN2, SH3TC2, GDAP1, LITAF, NEFL, FIG4, MORC2) plus
other genes that are standard, non-controversial entries in CMT gene
panels/GeneReviews-style overviews (e.g. RAB7A/CMT2B, GARS1/CMT2D,
HSPB1/CMT2F, DNM2/CMT2M, YARS1/CMT-DIC, EGR2/CMT1D, PRX/CMT4F,
FGD4/CMT4H, NDRG1/CMT4D, SBF2/CMT4B2). The taxonomy remains
admin-extensible at runtime via discovery_taxonomy -- this seed list is a
starting point, not a ceiling, and can be corrected/extended without a
deployment.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.taxonomy import DiscoveryTaxonomy

DEFAULT_TAXONOMY: list[dict] = [
    # ---- Disease terminology ----
    {"category": "disease_synonym", "term": "Charcot-Marie-Tooth", "synonyms": ["CMT", "Charcot Marie Tooth"]},
    {"category": "disease_synonym", "term": "hereditary motor sensory neuropathy", "synonyms": ["HMSN"]},
    {"category": "disease_synonym", "term": "hereditary neuropathy", "synonyms": []},
    {"category": "disease_synonym", "term": "Dejerine-Sottas disease", "synonyms": ["Dejerine-Sottas syndrome", "DSS"]},
    {"category": "disease_synonym", "term": "hereditary motor and sensory neuropathy", "synonyms": []},

    # ---- Genes (see module docstring re: provenance/non-exhaustiveness) ----
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
    {"category": "gene", "term": "RAB7A", "synonyms": []},
    {"category": "gene", "term": "GARS1", "synonyms": ["GARS"]},
    {"category": "gene", "term": "HSPB1", "synonyms": ["HSP27"]},
    {"category": "gene", "term": "HSPB8", "synonyms": []},
    {"category": "gene", "term": "DNM2", "synonyms": []},
    {"category": "gene", "term": "YARS1", "synonyms": ["YARS"]},
    {"category": "gene", "term": "EGR2", "synonyms": []},
    {"category": "gene", "term": "PRX", "synonyms": ["PRX (periaxin)"]},
    {"category": "gene", "term": "FGD4", "synonyms": []},
    {"category": "gene", "term": "NDRG1", "synonyms": []},
    {"category": "gene", "term": "SBF2", "synonyms": ["MTMR13"]},
    {"category": "gene", "term": "AARS1", "synonyms": ["AARS"]},
    {"category": "gene", "term": "BSCL2", "synonyms": []},
    {"category": "gene", "term": "LRSAM1", "synonyms": []},
    {"category": "gene", "term": "DYNC1H1", "synonyms": []},
    {"category": "gene", "term": "IGHMBP2", "synonyms": []},
    {"category": "gene", "term": "TRPV4", "synonyms": []},

    # ---- CMT subtypes (standard CMT1/2/3/4/X/DI nomenclature) ----
    {"category": "cmt_subtype", "term": "CMT1A", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT1B", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT1C", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT1D", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT1E", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT1F", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT2A", "synonyms": ["CMT2A1", "CMT2A2"]},
    {"category": "cmt_subtype", "term": "CMT2B", "synonyms": ["CMT2B1", "CMT2B2"]},
    {"category": "cmt_subtype", "term": "CMT2C", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT2D", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT2E", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT2F", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT2I", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT2J", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT2K", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT2L", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT2M", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT2N", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT2O", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT2P", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT2S", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT3", "synonyms": ["Dejerine-Sottas"]},
    {"category": "cmt_subtype", "term": "CMT4A", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT4B1", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT4B2", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT4C", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT4D", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT4E", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT4F", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT4H", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMT4J", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMTX1", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMTX2", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMTX3", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMTX4", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMTX5", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMTX6", "synonyms": []},
    {"category": "cmt_subtype", "term": "CMTDIA", "synonyms": ["CMT-DIA"]},
    {"category": "cmt_subtype", "term": "CMTDIB", "synonyms": ["CMT-DIB"]},
    {"category": "cmt_subtype", "term": "CMTDIC", "synonyms": ["CMT-DIC"]},
    {"category": "cmt_subtype", "term": "CMTDID", "synonyms": ["CMT-DID"]},

    # ---- Topics ----
    {"category": "topic", "term": "pathophysiology", "synonyms": []},
    {"category": "topic", "term": "genetics", "synonyms": ["genotype", "genetic testing"]},
    {"category": "topic", "term": "diagnosis", "synonyms": ["diagnostic criteria"]},
    {"category": "topic", "term": "natural history", "synonyms": []},
    {"category": "topic", "term": "biomarkers", "synonyms": ["biomarker"]},
    {"category": "topic", "term": "clinical outcome measures", "synonyms": ["CMTNS", "CMTPedS", "outcome measure"]},
    {"category": "topic", "term": "disease progression", "synonyms": []},
    {"category": "topic", "term": "therapies", "synonyms": ["treatment", "therapeutics"]},
    {"category": "topic", "term": "gene therapy", "synonyms": []},
    {"category": "topic", "term": "antisense oligonucleotides", "synonyms": ["ASO"]},
    {"category": "topic", "term": "small molecules", "synonyms": ["small-molecule therapy"]},
    {"category": "topic", "term": "rehabilitation", "synonyms": []},
    {"category": "topic", "term": "physiotherapy", "synonyms": ["physical therapy"]},
    {"category": "topic", "term": "orthotics", "synonyms": ["AFO", "ankle foot orthosis"]},
    {"category": "topic", "term": "assistive technology", "synonyms": ["assistive devices"]},
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
