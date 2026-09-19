"""
CMT-specific overhaul, Phase 2C: the deterministic CMT eligibility gate.

Covers the required eligibility test list:
  7. Non-CMT source record does not create a candidate (via the full
     worker pipeline -- see test_worker_jobs.py's
     test_worker_rejects_non_cmt_record_but_retains_it_for_provenance).
  8. CMT-specific source record creates a candidate (same file).
  9. Source record remains retained for provenance/deduplication when
     rejected (same file).
  10. Manual override behavior remains intact (already covered by
      test_candidate_engine.py -- unaffected since the gate only runs in
      the collector pipeline, not create_candidate_from_source_record
      itself; see app/api/routers/manual.py's documented decision not to
      gate manual submissions).

This file covers the gate's own logic directly (unit-level, no worker/DB
pipeline involved beyond the taxonomy lookup it needs).
"""
from app.collectors.base import NormalizedRecord
from app.services.intelligence.eligibility import assess_eligibility
from app.services.taxonomy_service import seed_default_taxonomy
from tests.fixtures.golden_dataset import (
    CLINICAL_TRIAL_RECRUITING,
    CMT_GENETIC_RESEARCH,
    CMT_SPECIFIC_RESEARCH,
    IRRELEVANT_CONTENT,
    PERIPHERAL_NEUROPATHY_RESEARCH,
)


def _assess(db, record: NormalizedRecord, conditions=None):
    return assess_eligibility(
        db, title=record.title, abstract_or_description=record.abstract or record.description,
        structured_conditions=conditions,
    )


def test_explicit_cmt_disease_name_is_eligible(db_session):
    seed_default_taxonomy(db_session)
    result = _assess(db_session, CMT_SPECIFIC_RESEARCH)
    assert result.eligible is True
    assert result.matched_full_form is True


def test_cmt_subtype_code_alone_is_eligible(db_session):
    seed_default_taxonomy(db_session)
    result = _assess(db_session, CMT_GENETIC_RESEARCH)  # title mentions "CMT4A"
    assert result.eligible is True


def test_generic_peripheral_neuropathy_alone_does_not_qualify(db_session):
    """Test list item 2: generic hereditary/peripheral neuropathy does not
    qualify by itself."""
    seed_default_taxonomy(db_session)
    result = _assess(db_session, PERIPHERAL_NEUROPATHY_RESEARCH)
    assert result.eligible is False
    assert "peripheral neuropathy" in result.matched_weak_terms
    assert result.matched_genes == []
    assert result.matched_strong_terms == []


def test_gene_only_mention_does_not_qualify(db_session):
    """Test list item 3: gene-only mention does not qualify -- constructed
    record deliberately mentions a validated CMT gene (MFN2) with no
    disease/subtype/neuropathy context at all."""
    seed_default_taxonomy(db_session)
    record = NormalizedRecord(
        external_id="pmid-gene-only",
        title="MFN2 regulates mitochondrial fusion dynamics in cultured fibroblasts",
        abstract="We characterized MFN2-dependent mitochondrial morphology changes in HeLa cells.",
    )
    result = _assess(db_session, record)
    assert result.eligible is False
    assert "MFN2" in result.matched_genes
    assert result.matched_strong_terms == []
    assert result.matched_weak_terms == []
    assert "gene-only" in result.reason


def test_generic_hereditary_neuropathy_plus_gene_no_longer_qualifies(db_session):
    """FINAL CORRECTIVE PROMPT #2: 'generic hereditary neuropathy + gene ->
    reject unless there is explicit CMT disease context.' This used to be
    the one case WEAK_CONTEXT_TERMS were allowed to promote to eligible;
    the corrective prompt explicitly closes that path -- gene evidence is
    supporting-only now, never an independent (or weak-context-assisted)
    eligibility driver."""
    seed_default_taxonomy(db_session)
    record = NormalizedRecord(
        external_id="pmid-gene-plus-context",
        title="MFN2 mutation identified in a family with hereditary neuropathy",
        abstract="Whole-exome sequencing identified a novel MFN2 variant segregating with hereditary neuropathy.",
    )
    result = _assess(db_session, record)
    assert result.eligible is False
    assert "MFN2" in result.matched_genes
    assert "hereditary neuropathy" in result.matched_weak_terms


def test_generic_peripheral_neuropathy_plus_gene_no_longer_qualifies(db_session):
    """Same rule, peripheral-neuropathy variant explicitly named in the
    corrective prompt."""
    seed_default_taxonomy(db_session)
    record = NormalizedRecord(
        external_id="pmid-gene-plus-peripheral",
        title="PMP22 duplication in a cohort with peripheral neuropathy",
        abstract="We report PMP22 copy-number findings in patients with peripheral neuropathy.",
    )
    result = _assess(db_session, record)
    assert result.eligible is False
    assert "PMP22" in result.matched_genes
    assert "peripheral neuropathy" in result.matched_weak_terms


def test_explicit_cmt_context_plus_gene_still_qualifies(db_session):
    """Corrective prompt #2: 'CMT-specific terminology/full form + relevant
    gene -> may qualify.' Full-form disease context plus a gene is still
    accepted -- the gene is supporting evidence on an already-qualifying
    record, not the reason it qualifies."""
    seed_default_taxonomy(db_session)
    record = NormalizedRecord(
        external_id="pmid-full-form-plus-gene",
        title="A GDAP1 variant in a family with Charcot-Marie-Tooth disease",
        abstract="Genetic analysis identified a GDAP1 variant segregating with Charcot-Marie-Tooth disease.",
    )
    result = _assess(db_session, record)
    assert result.eligible is True
    assert result.matched_full_form is True
    assert "GDAP1" in result.matched_genes


def test_bare_cmt_acronym_alone_does_not_qualify(db_session):
    """FINAL CORRECTIVE PROMPT #1: the bare acronym 'CMT' without an
    explicit Charcot-Marie-Tooth full-form reference elsewhere in the
    source text must NOT be treated as CMT evidence -- prevents
    acronym-only false positives (e.g. an unrelated use of "CMT")."""
    seed_default_taxonomy(db_session)
    record = NormalizedRecord(
        external_id="pmid-bare-acronym",
        title="Evaluating CMT as a biomarker candidate in an unrelated cohort study",
        abstract="This paper discusses the acronym CMT purely as a lab assay identifier, with no disease context.",
    )
    result = _assess(db_session, record)
    assert result.eligible is False
    assert result.matched_bare_acronym is True
    assert result.matched_full_form is False
    assert "acronym" in result.reason


def test_full_form_with_hyphen_qualifies(db_session):
    seed_default_taxonomy(db_session)
    record = NormalizedRecord(
        external_id="pmid-full-form-hyphen",
        title="Charcot-Marie-Tooth disease: a review of current management strategies",
    )
    result = _assess(db_session, record)
    assert result.eligible is True
    assert result.matched_full_form is True


def test_full_form_without_hyphen_qualifies(db_session):
    """The corrective prompt explicitly lists both spellings: 'Charcot
    Marie Tooth' (no hyphens) must also count as the qualifying full
    form."""
    seed_default_taxonomy(db_session)
    record = NormalizedRecord(
        external_id="pmid-full-form-no-hyphen",
        title="Charcot Marie Tooth disease and its clinical subtypes",
    )
    result = _assess(db_session, record)
    assert result.eligible is True
    assert result.matched_full_form is True


def test_incidental_cmt_mention_unrelated_to_study_is_rejected(db_session):
    """Test list #17: 'incidental CMT mention unrelated to the actual
    study' must be rejected. Simulates a trial whose actual focus is
    unrelated, but whose background text name-drops the bare acronym."""
    seed_default_taxonomy(db_session)
    record = NormalizedRecord(
        external_id="NCT-incidental-cmt",
        title="A Study of Cognitive Behavioral Therapy for Chronic Pain",
        description="Background literature mentions CMT among many other conditions studied historically.",
    )
    result = _assess(db_session, record)
    assert result.eligible is False


def test_irrelevant_content_is_not_eligible(db_session):
    seed_default_taxonomy(db_session)
    result = _assess(db_session, IRRELEVANT_CONTENT)
    assert result.eligible is False
    assert result.matched_strong_terms == []
    assert result.matched_weak_terms == []
    assert result.matched_genes == []


def test_clinical_trial_structured_conditions_considered(db_session):
    """Test list item 5: ClinicalTrials structured condition is
    considered -- a trial whose free text is generic but whose structured
    condition list explicitly names a CMT subtype is eligible."""
    seed_default_taxonomy(db_session)
    record = NormalizedRecord(
        external_id="NCT-structured-condition",
        title="A Study of an Investigational Therapy in Adults",  # deliberately generic title
        description="A randomized, double-blind study of an investigational oral therapy.",
    )
    result = _assess(db_session, record, conditions=["Charcot-Marie-Tooth Disease Type 1A"])
    assert result.eligible is True


def test_generic_neuropathy_clinical_trial_does_not_qualify(db_session):
    """Test list item 6: generic neuropathy clinical trial does not
    qualify -- structured conditions list a generic term only."""
    seed_default_taxonomy(db_session)
    record = NormalizedRecord(
        external_id="NCT-generic-neuropathy",
        title="A Study of Balance Training",
        description="A study of balance training interventions.",
    )
    result = _assess(db_session, record, conditions=["Peripheral Neuropathy"])
    assert result.eligible is False


def test_named_cmt_trial_fixture_is_eligible(db_session):
    seed_default_taxonomy(db_session)
    result = _assess(db_session, CLINICAL_TRIAL_RECRUITING)  # title mentions "CMT1A"
    assert result.eligible is True


def test_structured_conditions_take_priority_over_incidental_description(db_session):
    """FINAL CORRECTIVE PROMPT #3: 'do not allow a weak incidental mention
    buried in the description to override the absence of genuine CMT
    disease relevance' -- when structured conditions are present, they
    are evaluated on their own; an unrelated study whose free-text
    description happens to mention CMT/Charcot-Marie-Tooth incidentally
    must still be rejected because the trial's actual listed condition is
    not CMT."""
    seed_default_taxonomy(db_session)
    record = NormalizedRecord(
        external_id="NCT-incidental-description",
        title="A Study of Physical Therapy Techniques",
        description=(
            "This study builds on prior work; for comparison, Charcot-Marie-Tooth disease "
            "was studied using similar physical therapy outcome measures in an unrelated trial."
        ),
    )
    result = _assess(db_session, record, conditions=["Generalized Anxiety Disorder"])
    assert result.eligible is False


def test_structured_conditions_accept_even_with_generic_title(db_session):
    """The flip side: when the trial's OWN structured condition names CMT,
    it is accepted even though the title/description alone read generic
    (already covered by test_clinical_trial_structured_conditions_considered
    above; this variant explicitly checks the reason references the
    structured field)."""
    seed_default_taxonomy(db_session)
    record = NormalizedRecord(
        external_id="NCT-structured-priority",
        title="A Study of an Investigational Therapy",
        description="A randomized trial of an investigational therapy in adult participants.",
    )
    result = _assess(db_session, record, conditions=["Charcot-Marie-Tooth Disease"])
    assert result.eligible is True
    assert "structured condition" in result.reason.lower()
