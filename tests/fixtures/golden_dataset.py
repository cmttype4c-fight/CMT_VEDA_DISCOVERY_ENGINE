"""
Deterministic golden dataset (spec #46): ten fixture items covering the
full range of content the Discovery Engine must correctly classify,
including duplicate/updated-record edge cases (spec #12, #11).

Each entry is a `NormalizedRecord` -- the same shape every real collector
produces -- so these fixtures exercise normalization, deduplication,
candidate creation, and (with the mock AI provider) classification
end-to-end without any network access.
"""
from datetime import date

from app.collectors.base import NormalizedRecord

# 1. CMT-specific research
CMT_SPECIFIC_RESEARCH = NormalizedRecord(
    external_id="pmid-10000001",
    canonical_url="https://pubmed.ncbi.nlm.nih.gov/10000001/",
    title="Natural history of Charcot-Marie-Tooth disease type 1A in a longitudinal cohort",
    authors=["A. Researcher", "B. Scientist"],
    journal="Journal of the Peripheral Nervous System",
    doi="10.1000/cmt1a-natural-history",
    pmid="10000001",
    publication_date=date(2025, 3, 1),
    abstract=(
        "We followed 120 patients with Charcot-Marie-Tooth disease type 1A (CMT1A) caused by "
        "PMP22 duplication over five years to characterize disease progression."
    ),
)

# 2. CMT genetic research
CMT_GENETIC_RESEARCH = NormalizedRecord(
    external_id="pmid-10000002",
    canonical_url="https://pubmed.ncbi.nlm.nih.gov/10000002/",
    title="A novel GDAP1 variant causes autosomal recessive CMT4A",
    authors=["C. Geneticist"],
    journal="European Journal of Human Genetics",
    doi="10.1000/gdap1-cmt4a-variant",
    pmid="10000002",
    publication_date=date(2025, 5, 12),
    abstract="Whole-exome sequencing identified a novel pathogenic GDAP1 variant in three families with CMT4A.",
)

# 3. Hereditary neuropathy research (broader than CMT specifically)
HEREDITARY_NEUROPATHY_RESEARCH = NormalizedRecord(
    external_id="pmid-10000003",
    canonical_url="https://pubmed.ncbi.nlm.nih.gov/10000003/",
    title="Diagnostic yield of gene panel testing across hereditary neuropathy subtypes",
    authors=["D. Clinician"],
    journal="Neurology Genetics",
    doi="10.1000/hereditary-neuropathy-panel",
    pmid="10000003",
    publication_date=date(2024, 11, 20),
    abstract="We evaluated diagnostic yield of a 68-gene hereditary neuropathy panel including HMSN subtypes.",
)

# 4. Peripheral neuropathy research (broader still, not CMT-specific)
PERIPHERAL_NEUROPATHY_RESEARCH = NormalizedRecord(
    external_id="pmid-10000004",
    canonical_url="https://pubmed.ncbi.nlm.nih.gov/10000004/",
    title="Balance training outcomes in idiopathic peripheral neuropathy: a randomized trial",
    authors=["E. Therapist"],
    journal="Archives of Physical Medicine and Rehabilitation",
    doi="10.1000/balance-training-peripheral-neuropathy",
    pmid="10000004",
    publication_date=date(2024, 8, 4),
    abstract="A randomized trial of balance training in adults with idiopathic peripheral neuropathy of any cause.",
)

# 5. Broader neuromuscular research
BROADER_NEUROMUSCULAR_RESEARCH = NormalizedRecord(
    external_id="pmid-10000005",
    canonical_url="https://pubmed.ncbi.nlm.nih.gov/10000005/",
    title="Muscle imaging biomarkers across neuromuscular disorders",
    authors=["F. Radiologist"],
    journal="Muscle & Nerve",
    doi="10.1000/muscle-imaging-neuromuscular",
    pmid="10000005",
    publication_date=date(2024, 2, 18),
    abstract="MRI-based fat fraction quantification was compared across several neuromuscular disorders.",
)

# 6. Irrelevant content (general health, not neuropathy-related at all)
IRRELEVANT_CONTENT = NormalizedRecord(
    external_id="pmid-10000006",
    canonical_url="https://pubmed.ncbi.nlm.nih.gov/10000006/",
    title="Dietary sodium intake and cardiovascular risk in urban populations",
    authors=["G. Epidemiologist"],
    journal="American Journal of Cardiology",
    doi="10.1000/sodium-cardiovascular-risk",
    pmid="10000006",
    publication_date=date(2023, 9, 9),
    abstract="A cross-sectional study of dietary sodium intake and blood pressure in an urban cohort.",
)

# 7. Clinical trial (initial state: Recruiting)
CLINICAL_TRIAL_RECRUITING = NormalizedRecord(
    external_id="NCT90000001",
    canonical_url="https://clinicaltrials.gov/study/NCT90000001",
    title="A Phase 2 Study of Gene Therapy for CMT1A",
    clinical_trial_id="NCT90000001",
    publisher="Example Therapeutics Inc.",
    publication_date=date(2025, 1, 15),
    description="Phase 2 study evaluating an investigational gene therapy in adults with CMT1A.",
    raw_metadata={"source": "clinicaltrials.gov", "status": "RECRUITING"},
)

# 8. Research news (not a paper or trial)
RESEARCH_NEWS = NormalizedRecord(
    external_id="cmta-news-2025-04",
    canonical_url="https://www.cmtausa.org/news/example-research-news",
    title="CMTA-funded researchers publish promising CMT2 findings",
    publisher="CMT Association",
    publication_date=date(2025, 4, 2),
    description="A news summary of a recently published CMT2 study funded through the CMTA's STAR program.",
    raw_metadata={"source": "rss"},
)

# 9. Duplicate of #1 (same DOI, same content -> should classify as DUPLICATE)
DUPLICATE_OF_CMT_SPECIFIC = NormalizedRecord(
    external_id="pmid-10000001",  # collected again on a later run
    canonical_url="https://pubmed.ncbi.nlm.nih.gov/10000001/",
    title="Natural history of Charcot-Marie-Tooth disease type 1A in a longitudinal cohort",
    authors=["A. Researcher", "B. Scientist"],
    journal="Journal of the Peripheral Nervous System",
    doi="10.1000/cmt1a-natural-history",
    pmid="10000001",
    publication_date=date(2025, 3, 1),
    abstract=(
        "We followed 120 patients with Charcot-Marie-Tooth disease type 1A (CMT1A) caused by "
        "PMP22 duplication over five years to characterize disease progression."
    ),
)

# 10. Updated clinical trial (#7's NCT ID, status changed -> should classify as UPDATED)
CLINICAL_TRIAL_UPDATED_STATUS = NormalizedRecord(
    external_id="NCT90000001",
    canonical_url="https://clinicaltrials.gov/study/NCT90000001",
    title="A Phase 2 Study of Gene Therapy for CMT1A",
    clinical_trial_id="NCT90000001",
    publisher="Example Therapeutics Inc.",
    publication_date=date(2025, 1, 15),
    description="Phase 2 study evaluating an investigational gene therapy in adults with CMT1A.",
    raw_metadata={"source": "clinicaltrials.gov", "status": "ACTIVE_NOT_RECRUITING"},
)

# Rev4 classification guard: CMT-associated gene without CMT disease context.
GENE_ONLY_MFN2_RESEARCH = NormalizedRecord(
    external_id="pmid-10000007",
    canonical_url="https://pubmed.ncbi.nlm.nih.gov/10000007/",
    title="Mitochondrial dynamics in diabetic retinopathy",
    authors=["Test Researcher"],
    journal="Journal of Experimental Medicine",
    pmid="10000007",
    publication_date=date(2025, 6, 1),
    abstract=(
        "We investigated mitochondrial dysfunction in retinal cells and found that "
        "MFN2 expression was altered during diabetic stress."
    ),
)

# Rev4 classification guard: PMP22 mentioned in another neuromuscular disease.
GENE_ONLY_PMP22_RESEARCH = NormalizedRecord(
    external_id="pmid-10000008",
    canonical_url="https://pubmed.ncbi.nlm.nih.gov/10000008/",
    title="Schwann cell abnormalities in Duchenne muscular dystrophy",
    authors=["Test Researcher"],
    journal="Neuromuscular Disease Journal",
    pmid="10000008",
    publication_date=date(2025, 7, 1),
    abstract=(
        "We studied Schwann cell abnormalities in Duchenne muscular dystrophy. "
        "PMP22 expression was reduced in affected peripheral nerves."
    ),
)

ALL_RECORDS = [
    CMT_SPECIFIC_RESEARCH,
    CMT_GENETIC_RESEARCH,
    HEREDITARY_NEUROPATHY_RESEARCH,
    PERIPHERAL_NEUROPATHY_RESEARCH,
    BROADER_NEUROMUSCULAR_RESEARCH,
    IRRELEVANT_CONTENT,
    CLINICAL_TRIAL_RECRUITING,
    RESEARCH_NEWS,
]
