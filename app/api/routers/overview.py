"""
CMT Veda compatibility: GET /overview.

Pure aggregation over existing tables/columns -- no new dashboard data
model, per the instruction not to introduce a second one. Every count
here is a GROUP BY/COUNT against columns that already exist and are
already indexed for exactly this kind of query (content_type, scope,
newsletter_status, rag_status on discovery_candidates; status on
discovery_sources/discovery_runs; is_current/is_ai_generated on
discovery_editorial_drafts).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.auth import Principal, require_any_authenticated
from app.models.candidate import DiscoveryCandidate
from app.models.editorial import DiscoveryEditorialDraft
from app.models.run import DiscoveryRun
from app.models.source import DiscoverySource
from app.schemas.overview import (
    CandidateCounts,
    EditorialDraftCounts,
    OverviewOut,
    RunCounts,
    SourceCounts,
)

router = APIRouter(tags=["overview"])


def _counts_by(db: Session, column) -> dict[str, int]:
    rows = db.execute(select(column, func.count()).group_by(column)).all()
    return {str(value): count for value, count in rows if value is not None}


@router.get("/overview", response_model=OverviewOut)
def get_overview(db: Session = Depends(get_db), _: Principal = Depends(require_any_authenticated)):
    candidate_total = db.execute(select(func.count()).select_from(DiscoveryCandidate)).scalar_one()
    candidates = CandidateCounts(
        total=candidate_total,
        by_content_type=_counts_by(db, DiscoveryCandidate.content_type),
        by_newsletter_status=_counts_by(db, DiscoveryCandidate.newsletter_status),
        by_rag_status=_counts_by(db, DiscoveryCandidate.rag_status),
        by_scope=_counts_by(db, DiscoveryCandidate.scope),
    )

    source_total = db.execute(select(func.count()).select_from(DiscoverySource)).scalar_one()
    source_enabled = db.execute(
        select(func.count()).select_from(DiscoverySource).where(DiscoverySource.enabled.is_(True))
    ).scalar_one()
    sources = SourceCounts(total=source_total, enabled=source_enabled, disabled=source_total - source_enabled)

    run_total = db.execute(select(func.count()).select_from(DiscoveryRun)).scalar_one()
    last_run_at = db.execute(select(func.max(DiscoveryRun.completed_at))).scalar_one()
    runs = RunCounts(total=run_total, by_status=_counts_by(db, DiscoveryRun.status), last_run_at=last_run_at)

    draft_total = db.execute(select(func.count()).select_from(DiscoveryEditorialDraft)).scalar_one()
    draft_current = db.execute(
        select(func.count()).select_from(DiscoveryEditorialDraft).where(DiscoveryEditorialDraft.is_current.is_(True))
    ).scalar_one()
    draft_ai = db.execute(
        select(func.count()).select_from(DiscoveryEditorialDraft).where(DiscoveryEditorialDraft.is_ai_generated.is_(True))
    ).scalar_one()
    editorial_drafts = EditorialDraftCounts(total=draft_total, current=draft_current, ai_generated=draft_ai)

    return OverviewOut(
        candidates=candidates,
        sources=sources,
        runs=runs,
        editorial_drafts=editorial_drafts,
        generated_at=datetime.now(timezone.utc),
    )
