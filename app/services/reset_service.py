"""
Safe candidate-data reset (CMT-specific overhaul, Phase 1 of the reset
plan -- NOT executed by this pass; see IMPLEMENTATION_STATUS.md and the
Phase 0 audit for the confirmed FK-aware deletion order this implements).

CONFIRMED SCOPE (per explicit instruction): this reset CLEARS old
collection/runtime history, not just candidate-derived data --

    discovery_candidates, discovery_analysis, discovery_editorial_drafts,
    newsletter_items (for these candidates), rag_ingestion_requests,
    discovery_documents, discovery_source_records, discovery_runs,
    discovery_jobs

PRESERVED, never touched by this module:

    discovery_sources, discovery_taxonomy, discovery_audit_log,
    schema/migrations, auth/configuration

Deletion order respects every FK in the schema (children before parents):
newsletter_items -> rag_ingestion_requests -> discovery_editorial_drafts
-> discovery_analysis -> discovery_documents -> discovery_candidates, then
the source-side history (discovery_source_records -> discovery_runs),
then discovery_jobs (no FK to candidates, cleared independently so a
fresh run starts with an empty queue too).

`discovery_documents` (added per the "FINAL CORRECTIVE PROMPT" #12 --
"the safe reset must also correctly account for stored discovery
documents") has a NOT NULL foreign key to `discovery_candidates.id`, so
it must be deleted before candidates, same as analysis/editorial drafts.
Only the `discovery_documents` ROWS (metadata) are deleted here -- this
module has no filesystem access to the full-text storage volume and does
not attempt to delete the physical files on disk; those are orphaned by
a reset and would need a separate, explicit cleanup pass if disk space
recovery is ever required (out of scope here, since deleting files is a
different risk profile than deleting database rows and was not asked
for).

`discovery_audit_log.candidate_id` is NOT a foreign key (confirmed by
reading app/models/audit.py -- it's a plain indexed UUID column
specifically so audit history survives candidate deletion by design), so
it needs no special handling here and is never touched.

USAGE (all read/count operations are always safe to run; the actual
delete requires an explicit `confirm=True` and is never called by
anything in this codebase automatically):

    from app.database import session_scope
    from app.services.reset_service import dry_run_counts, execute_reset

    with session_scope() as db:
        print(dry_run_counts(db))          # safe, read-only, run this first

    with session_scope() as db:
        result = execute_reset(db, confirm=True)   # DESTRUCTIVE

A `pg_dump` backup is NOT something this module can take -- it has no
shell/OS access to the production database host from inside the
application process. See IMPLEMENTATION_STATUS.md for the exact backup
command to run manually, immediately before calling `execute_reset`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.analysis import DiscoveryAnalysis
from app.models.candidate import DiscoveryCandidate
from app.models.document import DiscoveryDocument
from app.models.editorial import DiscoveryEditorialDraft
from app.models.job import DiscoveryJob
from app.models.newsletter import NewsletterItem
from app.models.rag import RagIngestionRequest
from app.models.run import DiscoveryRun
from app.models.source_record import DiscoverySourceRecord

# Order matters -- children before parents, matching the FK graph exactly.
_TABLES_IN_DELETE_ORDER: list[tuple[str, type]] = [
    ("newsletter_items", NewsletterItem),
    ("rag_ingestion_requests", RagIngestionRequest),
    ("discovery_editorial_drafts", DiscoveryEditorialDraft),
    ("discovery_analysis", DiscoveryAnalysis),
    ("discovery_documents", DiscoveryDocument),
    ("discovery_candidates", DiscoveryCandidate),
    ("discovery_source_records", DiscoverySourceRecord),
    ("discovery_runs", DiscoveryRun),
    ("discovery_jobs", DiscoveryJob),
]

_PRESERVED_TABLES = [
    "discovery_sources", "discovery_taxonomy", "discovery_audit_log",
    "alembic_version (schema/migrations)", "auth/configuration (not a table -- env vars)",
]


@dataclass
class ResetCounts:
    counts: dict[str, int] = field(default_factory=dict)
    preserved: list[str] = field(default_factory=lambda: list(_PRESERVED_TABLES))

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def dry_run_counts(db: Session) -> ResetCounts:
    """Read-only. Always safe to call. Use this to see exactly what a
    reset would delete before ever calling execute_reset."""
    counts: dict[str, int] = {}
    for table_name, model in _TABLES_IN_DELETE_ORDER:
        counts[table_name] = db.execute(select(func.count()).select_from(model)).scalar_one()
    return ResetCounts(counts=counts)


@dataclass
class ResetResult:
    pre_counts: ResetCounts
    post_counts: ResetCounts
    verified_clean: bool


def execute_reset(db: Session, *, confirm: bool) -> ResetResult:
    """
    DESTRUCTIVE. Deletes every row in every table listed in
    _TABLES_IN_DELETE_ORDER. Never call this without:

      1. A verified pg_dump backup already taken (this function cannot
         take one itself -- no OS/shell access from the application
         process to the database host).
      2. Having already reviewed dry_run_counts(db) output.
      3. Explicit human sign-off for this specific run.

    Runs as a single transaction: the caller's session_scope() (or
    equivalent) commits only if this function returns normally, and
    rolls back entirely if anything raises -- so a failure partway
    through never leaves the database in a half-reset state.

    Deliberately requires `confirm=True` as a keyword-only argument (not
    a default) so it can never be invoked accidentally by a bare
    `execute_reset(db)` call.
    """
    if not confirm:
        raise ValueError(
            "execute_reset() called without confirm=True -- refusing to run. "
            "Call dry_run_counts(db) first, review it, take a pg_dump backup, "
            "then call execute_reset(db, confirm=True) explicitly."
        )

    pre_counts = dry_run_counts(db)

    for table_name, model in _TABLES_IN_DELETE_ORDER:
        db.execute(delete(model))
    db.flush()

    post_counts = dry_run_counts(db)
    verified_clean = post_counts.total == 0

    return ResetResult(pre_counts=pre_counts, post_counts=post_counts, verified_clean=verified_clean)
