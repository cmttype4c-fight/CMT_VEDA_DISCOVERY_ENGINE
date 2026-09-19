"""
Shared pytest fixtures.

Tests run against an in-memory SQLite database (StaticPool, single shared
connection) rather than PostgreSQL, so the full pipeline can be exercised
with zero external dependencies (spec #47: "prove the engine works using
fixtures/mocks" even when the environment cannot reach a real database).
The schema is portable by design (app/models/base.py) specifically to
make this possible; Alembic migrations (which target Postgres, spec #4)
are a separate, production-facing concern and are not exercised by these
tests -- see IMPLEMENTATION_STATUS.md.

AI_PROVIDER and RAG_ADAPTER default to "mock" (see .env.example), so
these tests never require network access or credentials.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("RAG_ADAPTER", "mock")
os.environ.setdefault("AUTH_ADMIN_TOKENS", "test-admin-token")
os.environ.setdefault("AUTH_REVIEWER_TOKENS", "test-reviewer-token")
os.environ.setdefault("AUTH_SERVICE_TOKENS", "test-service-token")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import enable_sqlite_savepoints
from app.models import Base


@pytest.fixture(scope="function")
def engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    enable_sqlite_savepoints(eng)  # required for db.begin_nested() (per-record isolation) to work on SQLite
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture(scope="function")
def db_session(engine):
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_local()
    yield session
    session.close()


@pytest.fixture(scope="function")
def app_client(engine):
    """FastAPI TestClient wired to the same in-memory engine as db_session,
    via a get_db dependency override (never touches a real DATABASE_URL)."""
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app.database import get_db
    from app.main import app

    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def _override_get_db():
        db = session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def admin_headers():
    return {"Authorization": "Bearer test-admin-token"}


@pytest.fixture
def reviewer_headers():
    return {"Authorization": "Bearer test-reviewer-token"}


@pytest.fixture
def service_headers():
    return {"Authorization": "Bearer test-service-token"}
