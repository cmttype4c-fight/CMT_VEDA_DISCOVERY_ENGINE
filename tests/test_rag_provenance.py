"""
CMT-specific overhaul, FINAL CORRECTIVE PROMPT #8/#9: RAG provenance
separation.

"The item sent to the CMT Veda RAG must be the original source/document,
not the AI-written news article." These tests assert, directly against
`_build_rag_metadata` (app/api/routers/rag.py), that:

  1. The metadata payload is built entirely from DiscoveryCandidate (the
     scientific record) and, when acquired, DiscoveryDocument (the
     original full-text file's own reference/provenance) -- never from
     DiscoveryEditorialDraft (the AI-written headline/summary).
  2. An editorial draft existing for the candidate has NO effect on the
     metadata payload's content -- proving the two are structurally
     independent, not just "currently" separate by omission.
  3. When full text has been acquired, its provenance (document_ref,
     content_hash, full_text_format, pdf_available, license_provenance)
     is present and traceable to the original source, not to the draft.
"""
from datetime import datetime, timezone

from app.api.routers.rag import _build_rag_metadata
from app.models.analysis import DiscoveryAnalysis
from app.models.document import DiscoveryDocument
from app.models.editorial import DiscoveryEditorialDraft
from app.models.source import DiscoverySource
from app.services.candidate_service import create_candidate_from_source_record
from app.services.deduplication import deduplicate
from app.services.normalization import normalize_to_source_record
from tests.fixtures.golden_dataset import CMT_SPECIFIC_RESEARCH


def _candidate(db):
    source = DiscoverySource(
        source_name="Test PubMed", source_type="pubmed", source_tier="tier_1",
        collection_method="official_api", configuration={"collector": "pubmed"},
    )
    db.add(source)
    db.flush()
    record = normalize_to_source_record(CMT_SPECIFIC_RESEARCH, source_id=source.id, source_type=source.source_type)
    result = deduplicate(db, record)
    return create_candidate_from_source_record(db, result.record, source)


def test_rag_metadata_contains_no_editorial_draft_fields(db_session):
    """The AI-written headline/summary must never appear in the RAG
    payload -- assert this both by field-name absence and by proving an
    attached editorial draft with deliberately distinctive text has zero
    effect on the metadata produced."""
    candidate = _candidate(db_session)

    analysis = DiscoveryAnalysis(
        candidate_id=candidate.id, analysis_version=1, is_latest=True,
        cmt_relevance_score=90, peripheral_neuropathy_relevance_score=60,
        clinical_relevance_score=70, research_importance_score=70,
        patient_relevance_score=70, analysis_confidence=80,
    )
    db_session.add(analysis)
    db_session.flush()

    draft = DiscoveryEditorialDraft(
        candidate_id=candidate.id, analysis_id=analysis.id, draft_version=1, is_current=True,
        headline="THIS AI-WRITTEN HEADLINE MUST NEVER APPEAR IN RAG METADATA",
        summary="THIS AI-WRITTEN SUMMARY MUST NEVER APPEAR IN RAG METADATA EITHER",
    )
    db_session.add(draft)
    db_session.flush()

    metadata_with_draft = _build_rag_metadata(candidate, "admin:a", db_session)

    payload_text = str(metadata_with_draft)
    assert "THIS AI-WRITTEN HEADLINE" not in payload_text
    assert "THIS AI-WRITTEN SUMMARY" not in payload_text
    assert "headline" not in metadata_with_draft
    assert "summary" not in metadata_with_draft

    # And the metadata is identical to what it would be without the draft
    # existing at all -- proving the draft has zero influence, not just
    # that these two specific strings happen to be absent.
    db_session.delete(draft)
    db_session.flush()
    metadata_without_draft = _build_rag_metadata(candidate, "admin:a", db_session)
    # approval_time differs by call (wall clock); compare everything else.
    metadata_with_draft.pop("approval_time", None)
    metadata_without_draft.pop("approval_time", None)
    assert metadata_with_draft == metadata_without_draft


def test_rag_metadata_full_text_block_points_to_original_document(db_session):
    """When full text is acquired, the metadata's full_text block must
    reference the ORIGINAL document's own provenance (DiscoveryDocument),
    not anything derived from the editorial draft."""
    candidate = _candidate(db_session)
    candidate.full_text_available = True
    db_session.flush()

    document = DiscoveryDocument(
        candidate_id=candidate.id, source="europepmc", full_text_source="europepmc_oa",
        retrieval_status="acquired", content_hash="original-source-hash-abc123",
        document_ref="/data/discovery-documents/or/original-source-hash-abc123.pdf",
        full_text_format="pdf", pdf_available=True,
        license_provenance="Europe PMC open-access full text (isOpenAccess=Y); see document_url for the original source.",
    )
    db_session.add(document)
    db_session.flush()

    metadata = _build_rag_metadata(candidate, "admin:a", db_session)

    assert metadata["full_text_available"] is True
    assert "full_text" in metadata
    assert metadata["full_text"]["content_hash"] == "original-source-hash-abc123"
    assert metadata["full_text"]["document_ref"] == document.document_ref
    assert metadata["full_text"]["full_text_format"] == "pdf"
    assert metadata["full_text"]["pdf_available"] is True
    assert "europepmc" in metadata["full_text"]["license_provenance"].lower()

    # Confirms the source data is the candidate's own scientific record
    # fields, not anything from an editorial draft.
    assert metadata["title"] == candidate.title
    assert metadata["doi"] == candidate.doi


def test_rag_metadata_without_full_text_still_valid(db_session):
    """A candidate with no acquired full text still produces a complete,
    valid metadata payload -- full_text_available=False, no full_text
    block, candidate itself unaffected (test list item 15 at the RAG
    metadata level)."""
    candidate = _candidate(db_session)
    assert candidate.full_text_available is False

    metadata = _build_rag_metadata(candidate, "admin:a", db_session)

    assert metadata["full_text_available"] is False
    assert "full_text" not in metadata
    assert metadata["title"] == candidate.title
