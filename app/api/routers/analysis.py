"""Veda Intelligence analysis API (spec #36)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_candidate_or_404, get_db
from app.auth import Principal, require_admin, require_any_authenticated
from app.models.analysis import DiscoveryAnalysis
from app.models.candidate import DiscoveryCandidate
from app.schemas.analysis import AnalysisOut, AnalysisRequest
from app.services.candidate_service import apply_analysis_to_candidate
from app.services.intelligence.veda_intelligence import analyse_candidate as run_analysis

router = APIRouter(tags=["analysis"])


@router.post("/candidates/{candidate_id}/analyse", response_model=AnalysisOut)
async def analyse_candidate(
    payload: AnalysisRequest = AnalysisRequest(),
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_admin),
):
    if not payload.force_reanalysis:
        existing = db.execute(
            select(DiscoveryAnalysis).where(
                DiscoveryAnalysis.candidate_id == candidate.id, DiscoveryAnalysis.is_latest.is_(True)
            )
        ).scalars().first()
        if existing:
            return existing

    analysis = await run_analysis(db, candidate)
    apply_analysis_to_candidate(db, candidate, analysis)
    db.commit()
    db.refresh(analysis)
    return analysis


@router.get("/candidates/{candidate_id}/analysis", response_model=AnalysisOut)
def get_analysis(
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_any_authenticated),
):
    analysis = db.execute(
        select(DiscoveryAnalysis).where(
            DiscoveryAnalysis.candidate_id == candidate.id, DiscoveryAnalysis.is_latest.is_(True)
        )
    ).scalars().first()
    if analysis is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No analysis yet for this candidate")
    return analysis
