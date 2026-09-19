"""
CMT eligibility gate (CMT-specific overhaul, Phase 2C).

Runs BEFORE candidate creation, in app/worker/handlers.py::handle_collect_source,
right after deduplicate() classifies a record as NEW. This is the change
that turns:

    source record -> deduplicate -> create candidate -> analysis

into:

    source record -> deduplicate -> CMT eligibility gate
        YES -> create candidate -> analysis
        NO  -> record retained (discovery_source_records.cmt_eligible=False,
               candidate_id stays NULL) for provenance/dedup, no candidate

Deliberately deterministic and AI-free (spec #2C: "the final candidate
eligibility must depend on contextual CMT relevance rather than the
presence of a keyword alone... do not use a single binary keyword test").
It runs at collection time, before the (async, AI-involving)
veda_intelligence analysis pass even exists for this record, so it has to
be cheap and rules-only -- it reuses the same taxonomy-matching primitive
(`app.services.intelligence.rules.term_matches`) the deterministic rules
stage already uses, just applied to raw collected text instead of an
already-created candidate.

THE CORE RULE THIS ENFORCES (per the "FINAL CORRECTIVE PROMPT", which
tightens the original gate further): a gene mention alone, or a generic
"hereditary neuropathy"/"peripheral neuropathy" mention alone -- even
combined with a gene -- must NOT be sufficient. The bare acronym "CMT" is
ALSO not sufficient by itself; it only counts as disease evidence when
the corpus also contains an explicit full-form reference
("Charcot-Marie-Tooth" / "Charcot Marie Tooth"), so an unrelated use of
the acronym (e.g. "cell-mediated toxicity", a trial ID, an abbreviation
for something else entirely) can't slip a record through. Only genuinely
CMT-specific evidence is:

  - an explicit CMT full-form disease-name term ("Charcot-Marie-Tooth" /
    "Charcot Marie Tooth"), OR
  - an unambiguous CMT clinical synonym that carries its own disease
    context without needing the acronym at all ("hereditary motor
    sensory neuropathy"/"HMSN" -- CMT's older clinical name, not a
    generic neuropathy term -- or Dejerine-Sottas, CMT's severe
    infantile-onset subtype), OR
  - an explicit CMT subtype code (CMT1A, CMT2A, CMTX1, etc. -- these are
    CMT-specific by definition, there is no non-CMT meaning).

A gene match (spec #2: "gene-based eligibility must also require CMT
context") is never an independent eligibility driver, and combining a
gene with only the generic/weak neuropathy terms is now explicitly
REJECTED unless one of the terms above is also present -- disease context
must come first, with gene/subtype evidence acting as supporting
detail on an already-qualifying record, never as the qualifying signal
itself. Everything else (gene-only, generic-neuropathy-only, bare "CMT"
acronym with no full-form context anywhere in the corpus, or no match at
all) is rejected. This is intentionally a whitelist, not a blacklist, so
a record matching nothing in the taxonomy is rejected by default rather
than silently admitted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.taxonomy import DiscoveryTaxonomy
from app.services.intelligence.rules import term_matches

# Terms that ARE, by themselves, specific enough to indicate this record is
# about CMT (not just "a neuropathy" in general, and not just an
# unqualified "CMT" acronym -- see FULL_FORM_TERMS below).
STRONG_DISEASE_TERMS: set[str] = {
    "hereditary motor sensory neuropathy",
    "hmsn",
    "dejerine-sottas disease",
    "dejerine-sottas syndrome",
    "dss",
}

# The disease's explicit full-form name. Presence of either of these is,
# on its own, sufficient strong evidence (per the corrective prompt's
# "accept: explicit Charcot-Marie-Tooth disease context"). This set is
# also what legitimizes a bare "CMT" acronym elsewhere in the same
# source text -- see ACRONYM_TERMS and _has_full_form_context() below.
FULL_FORM_TERMS: set[str] = {
    "charcot-marie-tooth",
    "charcot marie tooth",
}

# The bare acronym. NOT in STRONG_DISEASE_TERMS or FULL_FORM_TERMS: by
# itself it is not evidence of CMT relevance (spec: "CMT appearing
# incidentally or as an unrelated acronym" must not qualify). It is
# tracked separately purely so the eligibility reason can say plainly
# whether the acronym appeared without the qualifying full-form context.
ACRONYM_TERMS: set[str] = {"cmt"}

# Generic hereditary/peripheral-neuropathy terms: real evidence of a
# related clinical area, but explicitly NOT sufficient alone, and no
# longer sufficient even combined with a gene match per the corrective
# prompt ("generic hereditary neuropathy + gene -> reject unless there is
# explicit CMT disease context"). Retained only to produce a clearer
# rejection reason and for potential future use as supporting evidence
# on an already-qualifying record.
WEAK_CONTEXT_TERMS: set[str] = {
    "hereditary neuropathy",
    "hereditary motor and sensory neuropathy",
    "peripheral neuropathy",
}


@dataclass
class EligibilityResult:
    eligible: bool
    reason: str
    matched_strong_terms: list[str]
    matched_subtypes: list[str]
    matched_weak_terms: list[str]
    matched_genes: list[str]
    matched_full_form: bool = False
    matched_bare_acronym: bool = False

    def to_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "reason": self.reason,
            "matched_strong_terms": self.matched_strong_terms,
            "matched_subtypes": self.matched_subtypes,
            "matched_weak_terms": self.matched_weak_terms,
            "matched_genes": self.matched_genes,
            "matched_full_form": self.matched_full_form,
            "matched_bare_acronym": self.matched_bare_acronym,
        }


def _corpus(*parts: Optional[str]) -> str:
    return " \n ".join(p for p in parts if p).lower()


def assess_eligibility(
    db: Session,
    *,
    title: Optional[str],
    abstract_or_description: Optional[str] = None,
    structured_conditions: Optional[Iterable[str]] = None,
) -> EligibilityResult:
    """
    `structured_conditions` is for ClinicalTrials.gov (Phase 2B/corrective
    prompt #3): the trial's structured `conditionsModule.conditions`
    list, which is stronger, more precise evidence than a free-text hit
    alone ("the structured condition information should receive greater
    weight than incidental free-text mentions").

    When `structured_conditions` is provided and non-empty, it is treated
    as the PRIMARY evidence source and is evaluated on its own first: a
    trial whose actual listed condition names CMT is accepted from that
    alone. A trial whose structured conditions do NOT mention CMT is
    rejected even if the free-text title/description mentions it
    incidentally (e.g. in a background/reference section) -- that is
    exactly the "weak incidental mention buried in the description"
    case the corrective prompt says must not override the absence of
    genuine disease relevance. When no structured conditions are
    available at all (PubMed papers, or a trial with an empty
    conditions list), this falls back to evaluating title+abstract
    directly, unchanged from before.
    """
    if structured_conditions:
        condition_terms = [str(c) for c in structured_conditions if c]
        conditions_corpus = _corpus(*condition_terms)
        if conditions_corpus.strip():
            result = _assess_corpus(db, conditions_corpus, condition_source="structured_conditions")
            result.reason = f"[structured condition field] {result.reason}"
            return result
        # An empty-after-filtering conditions list is treated the same as
        # "no structured data" -- fall through to free text below.

    corpus = _corpus(title, abstract_or_description)
    result = _assess_corpus(db, corpus, condition_source="title_and_abstract")
    if structured_conditions is not None:
        # We were given a structured_conditions argument (a trial), but it
        # was empty -- note this so downstream logs/audits can see we
        # fell back to free text for a source that should normally have
        # structured data.
        result.reason = f"[no structured conditions available, evaluated title/abstract] {result.reason}"
    return result


def _assess_corpus(db: Session, corpus: str, *, condition_source: str) -> "EligibilityResult":

    matched_strong = sorted({t for t in STRONG_DISEASE_TERMS if term_matches(corpus, t, [])})
    matched_full_form = any(term_matches(corpus, t, []) for t in FULL_FORM_TERMS)
    matched_bare_acronym = any(term_matches(corpus, t, []) for t in ACRONYM_TERMS)
    matched_weak = sorted({t for t in WEAK_CONTEXT_TERMS if term_matches(corpus, t, [])})

    taxonomy_entries = db.execute(
        select(DiscoveryTaxonomy).where(DiscoveryTaxonomy.enabled.is_(True))
    ).scalars().all()

    matched_subtypes: list[str] = []
    matched_genes: list[str] = []
    for entry in taxonomy_entries:
        if not term_matches(corpus, entry.term, entry.synonyms):
            continue
        if entry.category == "cmt_subtype":
            matched_subtypes.append(entry.term)
        elif entry.category == "gene":
            matched_genes.append(entry.term)
        # disease_synonym entries are intentionally NOT consulted here --
        # STRONG_DISEASE_TERMS/FULL_FORM_TERMS/WEAK_CONTEXT_TERMS above is
        # the authoritative, hand-reviewed split of that category for
        # eligibility purposes specifically (see module docstring: a
        # generic "hereditary neuropathy" disease_synonym entry must NOT
        # be sufficient alone even though it *is* sufficient for the
        # rules stage's classification-tagging purpose).

    matched_subtypes = sorted(set(matched_subtypes))
    matched_genes = sorted(set(matched_genes))

    # Disease-context-first: the ONLY paths to eligibility are (a) the
    # disease's explicit full-form name, (b) an unambiguous clinical
    # synonym that carries CMT context without needing the acronym at
    # all, or (c) an explicit CMT subtype code. A gene match is never an
    # independent driver, and per the corrective prompt, gene + weak
    # generic-neuropathy context is NO LONGER sufficient on its own --
    # disease context must be established first.
    if matched_full_form:
        reason = "explicit Charcot-Marie-Tooth full-form disease name matched"
        return EligibilityResult(
            True, reason, matched_strong, matched_subtypes, matched_weak, matched_genes,
            matched_full_form=True, matched_bare_acronym=matched_bare_acronym,
        )

    if matched_strong:
        reason = "unambiguous CMT clinical synonym matched (e.g. HMSN, Dejerine-Sottas)"
        return EligibilityResult(
            True, reason, matched_strong, matched_subtypes, matched_weak, matched_genes,
            matched_full_form=False, matched_bare_acronym=matched_bare_acronym,
        )

    if matched_subtypes:
        reason = "explicit CMT subtype code matched"
        return EligibilityResult(
            True, reason, matched_strong, matched_subtypes, matched_weak, matched_genes,
            matched_full_form=False, matched_bare_acronym=matched_bare_acronym,
        )

    if matched_bare_acronym:
        reason = (
            "the acronym 'CMT' appears without an explicit Charcot-Marie-Tooth "
            "full-form reference elsewhere in the source text -- incidental/unrelated "
            "acronym use is not treated as CMT evidence"
        )
    elif matched_genes and matched_weak:
        reason = (
            "generic hereditary/peripheral neuropathy context combined with a gene match, "
            "but with no explicit CMT disease context -- insufficient per the strict "
            "disease-context-first rule (gene and neuropathy-context evidence alone, even "
            "together, do not qualify)"
        )
    elif matched_genes:
        reason = "gene-only match with no CMT-specific disease/subtype context -- insufficient alone"
    elif matched_weak:
        reason = "generic hereditary/peripheral neuropathy mention without CMT-specific disease, subtype, or full-form evidence"
    else:
        reason = "no CMT-specific disease, subtype, or full-form evidence found"

    return EligibilityResult(
        False, reason, matched_strong, matched_subtypes, matched_weak, matched_genes,
        matched_full_form=False, matched_bare_acronym=matched_bare_acronym,
    )
