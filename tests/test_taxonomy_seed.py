"""
CMT-specific overhaul, Phase 1: regression tests locking in

  1. seed_default_taxonomy() is idempotent (a re-run inserts nothing new)
  2. the expanded taxonomy actually contains the category coverage the
     Phase 1 plan called for (disease terms, genes, CMT subtypes, topics)
  3. app_client (a FastAPI TestClient, which fires app.main's startup
     event) still boots cleanly now that a startup handler calls
     seed_default_taxonomy -- this is the regression that matters most:
     the startup handler must not break every other test that uses
     app_client, since it runs against a different, unmigrated engine in
     that fixture (see app/main.py's docstring on the try/except there).
"""
from sqlalchemy import select

from app.models.taxonomy import DiscoveryTaxonomy
from app.services.taxonomy_service import DEFAULT_TAXONOMY, seed_default_taxonomy


def test_seed_default_taxonomy_is_idempotent(db_session):
    first_pass = seed_default_taxonomy(db_session)
    assert first_pass == len(DEFAULT_TAXONOMY)

    second_pass = seed_default_taxonomy(db_session)
    assert second_pass == 0

    total_rows = db_session.execute(select(DiscoveryTaxonomy)).scalars().all()
    assert len(total_rows) == len(DEFAULT_TAXONOMY)


def test_seed_default_taxonomy_covers_all_required_categories(db_session):
    seed_default_taxonomy(db_session)
    categories = {
        row.category
        for row in db_session.execute(select(DiscoveryTaxonomy.category)).all()
    }
    assert categories == {"disease_synonym", "gene", "cmt_subtype", "topic"}

    subtypes = {
        row.term
        for row in db_session.execute(
            select(DiscoveryTaxonomy.term).where(DiscoveryTaxonomy.category == "cmt_subtype")
        ).all()
    }
    # Not just the original 5 -- the CMT1/2/3/4/X/DI numbering families all present.
    assert {"CMT1A", "CMT2A", "CMT3", "CMT4C", "CMTX1", "CMTDIA"}.issubset(subtypes)

    topics = {
        row.term
        for row in db_session.execute(
            select(DiscoveryTaxonomy.term).where(DiscoveryTaxonomy.category == "topic")
        ).all()
    }
    assert {"gene therapy", "antisense oligonucleotides", "biomarkers", "clinical outcome measures"}.issubset(topics)


def test_seed_default_taxonomy_no_duplicate_category_term_pairs():
    seen = set()
    for item in DEFAULT_TAXONOMY:
        key = (item["category"], item["term"])
        assert key not in seen, f"duplicate taxonomy entry: {key}"
        seen.add(key)


def test_app_client_starts_cleanly_with_taxonomy_startup_hook(app_client):
    """
    Regression guard for the app.main startup event added in Phase 1: it
    must not prevent the API from starting even though it runs against a
    different (here, unmigrated) engine than app_client's dependency
    override targets. If this test fails, every other test using
    app_client would fail too -- this isolates that failure mode
    specifically.
    """
    resp = app_client.get("/health")
    assert resp.status_code == 200


def test_readiness_endpoint_surfaces_empty_taxonomy_as_a_visible_warning(engine, app_client):
    """FINAL CORRECTIVE PROMPT #13: 'do not silently swallow production
    initialization failures... make the problem visible through
    health/diagnostic mechanisms.' /readiness queries discovery_taxonomy
    LIVE via the same get_db dependency app_client's other endpoints use
    (unlike the startup hook, which runs against a separate, unmigrated
    production engine in tests) -- so an empty taxonomy is visible here
    regardless of what the startup hook itself believed happened."""
    resp = app_client.get("/readiness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["taxonomy_seeded"] is False
    assert any("taxonomy_not_seeded" in w for w in body["warnings"])
    # A data-availability problem doesn't flip the overall readiness probe
    # (see app/api/routers/health.py docstring) -- the process is healthy,
    # the DATA is what's missing.
    assert body["status"] == "ready"
    assert body["database"] is True


def test_readiness_endpoint_clears_warning_once_taxonomy_is_seeded(engine, app_client):
    from sqlalchemy.orm import sessionmaker

    from app.services.taxonomy_service import seed_default_taxonomy

    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with session_local() as db:
        seed_default_taxonomy(db)
        db.commit()

    resp = app_client.get("/readiness")
    body = resp.json()
    assert body["taxonomy_seeded"] is True
    assert body["warnings"] == []


def test_rules_stage_still_matches_after_expanded_taxonomy(db_session):
    """Confirms the Phase 1 taxonomy expansion doesn't disturb the existing
    rules-stage matching behavior tested in test_intelligence.py -- the
    original gene entries are still present and still matchable."""
    from app.services.candidate_service import create_candidate_from_source_record
    from app.services.deduplication import deduplicate
    from app.services.intelligence.rules import run_rules_stage
    from app.services.normalization import normalize_to_source_record
    from app.models.source import DiscoverySource
    from tests.fixtures.golden_dataset import CMT_GENETIC_RESEARCH

    seed_default_taxonomy(db_session)

    source = DiscoverySource(
        source_name="Test PubMed", source_type="pubmed", source_tier="tier_1",
        collection_method="official_api", configuration={"collector": "pubmed"},
    )
    db_session.add(source)
    db_session.flush()
    record = normalize_to_source_record(CMT_GENETIC_RESEARCH, source_id=source.id, source_type=source.source_type)
    result = deduplicate(db_session, record)
    candidate = create_candidate_from_source_record(db_session, result.record, source)

    rules_result = run_rules_stage(db_session, candidate)
    assert "GDAP1" in rules_result.matched_genes
