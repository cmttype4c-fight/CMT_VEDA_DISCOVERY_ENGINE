"""Shared FastAPI dependencies: DB session re-export + common lookups
that raise clean 404s (spec #36 error responses)."""
from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.candidate import DiscoveryCandidate
from app.models.source import DiscoverySource

__all__ = ["get_db", "get_candidate_or_404", "get_source_or_404"]


def get_candidate_or_404(candidate_id: uuid.UUID, db: Session = Depends(get_db)) -> DiscoveryCandidate:
    candidate = db.get(DiscoveryCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Candidate {candidate_id} not found")
    return candidate


def get_source_or_404(source_id: uuid.UUID, db: Session = Depends(get_db)) -> DiscoverySource:
    source = db.get(DiscoverySource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Source {source_id} not found")
    return source
