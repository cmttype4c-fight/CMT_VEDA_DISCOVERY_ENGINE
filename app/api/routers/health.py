"""Health endpoints (spec #43). Never expose credentials or internals."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.taxonomy import DiscoveryTaxonomy

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

    # CMT-specific overhaul, corrective prompt #13: "do not silently
    # swallow production initialization failures with a generic warning
    # and continue as though everything is healthy... failures should
    # produce an ERROR-level diagnostic and make the problem visible
    # through health/diagnostic mechanisms." The startup hooks in
    # app/main.py and app/worker/worker.py still can't be allowed to
    # crash the process on a transient failure, so this is the visible
    # side of that: `/readiness` queries the ACTUAL discovery_taxonomy
    # row count live, on every call, independent of whether the startup
    # hook itself believed it succeeded. An empty taxonomy in production
    # is exactly the failure mode that caused the original noise-ratio
    # bug (see IMPLEMENTATION_STATUS.md) -- this makes that condition
    # impossible to miss on a status dashboard or uptime check, without
    # making every request 500 or making local dev/test setups noisy.
    taxonomy_seeded = False
    if db_ok:
        try:
            taxonomy_count = db.execute(select(func.count()).select_from(DiscoveryTaxonomy)).scalar_one()
            taxonomy_seeded = taxonomy_count > 0
        except Exception:  # noqa: BLE001 -- table may not exist yet (pre-migration); reported as not-seeded, not a crash
            taxonomy_seeded = False

    # Deliberately does NOT fold `taxonomy_seeded` into `status` -- a
    # readiness probe flipping "not_ready" is often wired to restart or
    # take the instance out of rotation, and an empty taxonomy is a data
    # problem, not a process-health problem (the API is otherwise fully
    # functional; classification quality degrades, nothing crashes).
    # Surfacing it as a visible warning, rather than tying it to the
    # pass/fail probe outcome, is what makes it seen without turning a
    # data issue into an availability incident.
    warnings: list[str] = []
    if db_ok and not taxonomy_seeded:
        warnings.append(
            "taxonomy_not_seeded: discovery_taxonomy has zero rows -- CMT eligibility/rules "
            "matching will reject everything. Startup taxonomy seeding may have failed; check "
            "logs for taxonomy_seed_on_startup_failed."
        )

    return {
        "status": "ready" if db_ok else "not_ready",
        "database": db_ok,
        "taxonomy_seeded": taxonomy_seeded,
        "warnings": warnings,
    }
