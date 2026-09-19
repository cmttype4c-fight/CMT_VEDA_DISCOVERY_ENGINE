from app.models.document import DiscoveryDocument
from app.models.source import DiscoverySource
from app.services.candidate_service import create_candidate_from_source_record
from app.services.deduplication import deduplicate
from app.services.intelligence.ai_provider import AIProvider, AIResponse
from app.services.intelligence.editorial_service import generate_editorial_draft, update_draft_manually
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


class _CapturingProvider(AIProvider):
    """Test double that records the exact prompts it was called with, so
    tests can assert on prompt CONTENT (does it actually contain the
    extracted full text?) rather than only on the draft's output fields."""

    def __init__(self):
        self.last_system_prompt: str | None = None
        self.last_user_prompt: str | None = None

    async def complete_json(self, *, system_prompt: str, user_prompt: str, max_tokens: int = 1000) -> AIResponse:
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt
        return AIResponse(
            raw_text="{}",
            parsed_json={
                "headline": "Captured headline", "summary": "Captured summary",
                "why_it_matters": None, "key_points": [], "detailed_content": None,
                "cmt_relevance_explanation": None, "limitations": None,
            },
            model_name="test-capturing-provider",
        )


async def test_generate_editorial_draft_is_source_grounded_and_disclaimed(db_session):
    candidate = _candidate(db_session)
    draft = await generate_editorial_draft(db_session, candidate)

    assert draft.headline
    assert draft.summary
    assert draft.disclaimer  # non-diagnostic/non-prescriptive boilerplate always present
    assert draft.draft_version == 1
    assert draft.is_current is True
    assert draft.is_ai_generated is True
    assert draft.generation_model == "mock-heuristic"
    # References point back to the actual source -- never fabricated.
    assert any(r["url"] == candidate.source_url for r in draft.references) or candidate.source_url is None


async def test_second_generation_supersedes_first_version(db_session):
    candidate = _candidate(db_session)
    first = await generate_editorial_draft(db_session, candidate)
    second = await generate_editorial_draft(db_session, candidate)

    assert second.draft_version == first.draft_version + 1
    assert second.is_current is True
    db_session.refresh(first)
    assert first.is_current is False


async def test_manual_edit_creates_new_version_and_marks_not_ai_generated(db_session):
    candidate = _candidate(db_session)
    original = await generate_editorial_draft(db_session, candidate)

    edited = update_draft_manually(
        db_session, original, {"headline": "Editor-revised headline", "draft_status": "in_review"}, edited_by="reviewer:jane"
    )

    assert edited.headline == "Editor-revised headline"
    assert edited.draft_version == original.draft_version + 1
    assert edited.is_ai_generated is False
    assert edited.last_edited_by == "reviewer:jane"
    # The original AI-generated draft is preserved, not mutated (spec #20).
    db_session.refresh(original)
    assert original.headline != "Editor-revised headline"
    assert original.is_current is False


# --- full-text feeding Gemini (FINAL CORRECTIVE PROMPT #1) ---

def _acquired_document(db, candidate, *, extracted_text="Full extracted article text about CMT1A cohort outcomes."):
    doc = DiscoveryDocument(
        candidate_id=candidate.id, source="europepmc", retrieval_status="acquired",
        full_text_format="pdf", pdf_available=True,
        content_hash="hash-for-editorial-test", document_ref="/data/discovery-documents/ha/hash-for-editorial-test.pdf",
        extracted_text=extracted_text, extracted_char_count=len(extracted_text) if extracted_text else None,
        extraction_status="success" if extracted_text else "failed",
        extraction_error=None if extracted_text else "no text found",
    )
    db.add(doc)
    db.flush()
    return doc


async def test_editorial_draft_without_full_text_has_used_full_text_false(db_session):
    """Baseline: no acquired document at all -- generation falls back to
    the abstract as before, and provenance correctly records that."""
    candidate = _candidate(db_session)
    draft = await generate_editorial_draft(db_session, candidate)

    assert draft.used_full_text is False
    assert draft.source_document_id is None


async def test_editorial_draft_uses_acquired_full_text_as_primary_source(db_session):
    """FINAL CORRECTIVE PROMPT #1's core requirement: when full text is
    available, Gemini must actually RECEIVE it in the prompt, and the
    draft's provenance fields must reflect that."""
    candidate = _candidate(db_session)
    document = _acquired_document(db_session, candidate)

    provider = _CapturingProvider()
    draft = await generate_editorial_draft(db_session, candidate, provider=provider)

    assert "Full extracted article text about CMT1A cohort outcomes." in provider.last_user_prompt
    assert "PRIMARY source material" in provider.last_user_prompt
    assert draft.used_full_text is True
    assert draft.source_document_id == document.id


async def test_editorial_draft_falls_back_to_abstract_when_extraction_failed(db_session):
    """A document was acquired (the file itself exists) but text
    extraction failed (e.g. a scanned/image-only PDF) -- generation must
    fall back to the abstract rather than passing an empty/unusable
    string, and provenance must correctly show used_full_text=False."""
    candidate = _candidate(db_session)
    _acquired_document(db_session, candidate, extracted_text=None)

    provider = _CapturingProvider()
    draft = await generate_editorial_draft(db_session, candidate, provider=provider)

    assert "PRIMARY source material" not in provider.last_user_prompt
    assert draft.used_full_text is False
    assert draft.source_document_id is None


async def test_editorial_draft_still_includes_abstract_alongside_full_text(db_session):
    """The abstract stays in the prompt as a concise supplement even when
    full text is present -- only the SYSTEM_PROMPT instruction and the
    labeled block change to make clear which one is primary."""
    candidate = _candidate(db_session)
    _acquired_document(db_session, candidate)

    provider = _CapturingProvider()
    await generate_editorial_draft(db_session, candidate, provider=provider)

    assert "Abstract:" in provider.last_user_prompt
    assert (candidate.abstract or "") in provider.last_user_prompt


async def test_manual_edit_preserves_full_text_provenance(db_session):
    """A human edit to an AI draft must not silently lose the record of
    what source material the original AI generation was based on."""
    candidate = _candidate(db_session)
    document = _acquired_document(db_session, candidate)

    provider = _CapturingProvider()
    original = await generate_editorial_draft(db_session, candidate, provider=provider)
    assert original.used_full_text is True

    edited = update_draft_manually(db_session, original, {"headline": "Edited"}, edited_by="reviewer:jane")

    assert edited.used_full_text is True
    assert edited.source_document_id == document.id
