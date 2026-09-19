"""scheduler: calendar-cycle tracking for the 06:00 Asia/Kolkata scheduler

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-19

FINAL FOCUSED CORRECTION -- SCHEDULER, SOURCE IMPLEMENTATION, TESTS &
COMPLETE ZIP, TASK 1: due-ness for the global scheduler must be based on
the fixed 06:00 Asia/Kolkata *scheduled cycle* a source last participated
in, not on `last_run_at` (a collection-completion timestamp, which can
land at any time after 06:00 depending on how long collection took --
using it directly caused a daily source's effective cadence to drift and
could make it skip a day entirely; see app/worker/scheduler.py's module
docstring for the worked example).

Entirely additive: one new nullable column, no existing column renamed,
dropped, or reinterpreted. `discovery_sources.last_run_at` and
`last_success_at` are unchanged and keep their existing meaning
(collection-completion bookkeeping/observability) -- only *scheduling*
due-ness moved off of them.

This migration (like 0002 before it) has not been applied to any real
database as of this revision -- see IMPLEMENTATION_STATUS.md.
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "discovery_sources",
        sa.Column("last_scheduled_cycle_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("discovery_sources", "last_scheduled_cycle_at")
