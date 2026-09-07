from sqlalchemy import select

from app.models.audit import DiscoveryAuditLog
from app.models.enums import AuditAction
from app.models.source import DiscoverySource
from app.services.audit_service import write_audit_log
from app.services.candidate_service import create_candidate_from_source_record
from app.services.deduplication import deduplicate
from app.services.normalization import normalize_to_source_record
from tests.fixtures.golden_dataset import CMT_SPECIFIC_RESEARCH


def test_write_audit_log_persists_all_fields(db_session):
    entry = write_audit_log(
        db_session,
        action=AuditAction.discovered,
        performed_by="collector:test",
        old_value={"a": 1},
        new_value={"a": 2},
        notes="test note",
    )
    fetched = db_session.get(DiscoveryAuditLog, entry.id)
    assert fetched.action == "discovered"
    assert fetched.performed_by == "collector:test"
    assert fetched.old_value == {"a": 1}
    assert fetched.new_value == {"a": 2}
    assert fetched.notes == "test note"
    assert fetched.performed_at is not None


def test_candidate_discovery_is_automatically_audited(db_session):
    source = DiscoverySource(
        source_name="Test PubMed", source_type="pubmed", source_tier="tier_1",
        collection_method="official_api", configuration={"collector": "pubmed"},
    )
    db_session.add(source)
    db_session.flush()
    record = normalize_to_source_record(CMT_SPECIFIC_RESEARCH, source_id=source.id, source_type=source.source_type)
    result = deduplicate(db_session, record)
    candidate = create_candidate_from_source_record(db_session, result.record, source)

    logs = db_session.execute(
        select(DiscoveryAuditLog).where(DiscoveryAuditLog.candidate_id == candidate.id)
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].action == "discovered"


def test_audit_log_is_append_only_by_convention(db_session):
    """There is no update/delete function in audit_service -- only
    write_audit_log. This test documents that invariant by checking the
    module's public surface."""
    import app.services.audit_service as audit_service

    public_names = [n for n in dir(audit_service) if not n.startswith("_")]
    mutating_names = [n for n in public_names if n.lower() in ("update_audit_log", "delete_audit_log")]
    assert mutating_names == []
