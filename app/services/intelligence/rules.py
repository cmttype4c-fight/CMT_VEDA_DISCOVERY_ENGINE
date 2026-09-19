"""
Deterministic rules / parsing stage (spec #17 pipeline step 1).

Runs before the AI stage and produces structured, fully-explainable
matches from the candidate's own text (title/abstract) against the
admin-extendable taxonomy (spec #9). This output is:
  1. merged with the AI stage's proposal (AI can add nuance/topics the
     rules can't catch, but never removes a deterministic gene/subtype hit
     the rules already found with high confidence), and
  2. stored alongside the AI output on discovery_analysis.rules_output
     for full traceability/audit.

Keeping this step separate and deterministic is what lets the pipeline
avoid "sending completely unstructured raw content directly to an AI and
blindly trusting the response" (spec #17).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate import DiscoveryCandidate
from app.models.taxonomy import DiscoveryTaxonomy


@dataclass
class RulesResult:
    matched_genes: list[str] = field(default_factory=list)
    matched_subtypes: list[str] = field(default_factory=list)
    matched_topics: list[str] = field(default_factory=list)
    matched_disease_terms: list[str] = field(default_factory=list)
    taxonomy_version: str = "v1"

    def to_dict(self) -> dict:
        return {
            "matched_genes": self.matched_genes,
            "matched_subtypes": self.matched_subtypes,
            "matched_topics": self.matched_topics,
            "matched_disease_terms": self.matched_disease_terms,
            "taxonomy_version": self.taxonomy_version,
        }


def _text_corpus(candidate: DiscoveryCandidate) -> str:
    parts = [candidate.title or "", candidate.abstract or "", candidate.journal or ""]
    return " \n ".join(parts).lower()


def term_matches(corpus: str, term: str, synonyms: list[str]) -> bool:
    """Word-boundary-safe match of `term` (or any of its `synonyms`)
    against an already-lowercased `corpus`. Public (used by both this
    module and app/services/intelligence/eligibility.py's pre-candidate
    gate -- CMT-specific overhaul, Phase 2) so both stages share one
    definition of "does this text mention this taxonomy entry"."""
    variants = [term] + list(synonyms or [])
    for variant in variants:
        if not variant:
            continue
        pattern = r"(?<![a-z0-9])" + re.escape(variant.lower()) + r"(?![a-z0-9])"
        if re.search(pattern, corpus):
            return True
    return False


# Backward-compatible alias -- this module's own call site below used the
# private name before it was made public for reuse by eligibility.py.
_term_matches = term_matches


def run_rules_stage(db: Session, candidate: DiscoveryCandidate) -> RulesResult:
    corpus = _text_corpus(candidate)
    result = RulesResult()

    taxonomy_entries = db.execute(
        select(DiscoveryTaxonomy).where(DiscoveryTaxonomy.enabled.is_(True))
    ).scalars().all()

    if not taxonomy_entries:
        return result

    result.taxonomy_version = taxonomy_entries[0].taxonomy_version

    for entry in taxonomy_entries:
        if not _term_matches(corpus, entry.term, entry.synonyms):
            continue
        if entry.category == "gene":
            result.matched_genes.append(entry.term)
        elif entry.category == "cmt_subtype":
            result.matched_subtypes.append(entry.term)
        elif entry.category == "topic":
            result.matched_topics.append(entry.term)
        elif entry.category == "disease_synonym":
            result.matched_disease_terms.append(entry.term)

    return result
