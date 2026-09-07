"""
SQLAlchemy engine/session setup.

The engine URL is dialect-agnostic (postgresql+psycopg for production;
sqlite is supported for fast local/unit testing -- see tests/conftest.py).
Models avoid Postgres-only column types directly; instead they use portable
TypeDecorators (see app/models/base.py) so the same models work against
SQLite in tests and PostgreSQL in production/staging.
"""
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()

_connect_args = {}
if settings.database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=_connect_args)


def enable_sqlite_savepoints(target_engine: Engine) -> None:
    """
    Make `Session.begin_nested()` (SAVEPOINT) actually work correctly on
    SQLite. Used by the worker's per-record failure isolation
    (app/worker/handlers.py) to roll back a single failed record without
    disturbing the rest of the transaction.

    Without this, pysqlite's own implicit transaction handling conflicts
    with SQLAlchemy's SAVEPOINT emission -- a well-documented gotcha, see:
    https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#serializable-isolation-savepoints-transactional-ddl

    No-op for any non-SQLite dialect (PostgreSQL supports SAVEPOINT
    natively with no such workaround needed).
    """
    if target_engine.dialect.name != "sqlite":
        return

    @event.listens_for(target_engine, "connect")
    def _sqlite_disable_pysqlite_txn(dbapi_connection, connection_record):
        dbapi_connection.isolation_level = None

    @event.listens_for(target_engine, "begin")
    def _sqlite_emit_begin(conn):
        conn.exec_driver_sql("BEGIN")


enable_sqlite_savepoints(engine)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context manager for use outside of FastAPI (worker, scripts, tests)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
