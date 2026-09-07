"""Health endpoints (spec #43). Never expose credentials or internals."""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/readiness")
def readiness(db: Session = Depends(get_db)) -> dict:
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    return {"status": "ready" if db_ok else "not_ready", "database": db_ok}
