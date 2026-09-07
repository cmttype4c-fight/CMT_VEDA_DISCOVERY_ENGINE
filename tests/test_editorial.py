from app.models.source import DiscoverySource
from app.services.candidate_service import create_candidate_from_source_record
from app.services.deduplication import deduplicate
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
