"""CMT Veda Discovery Engine -- Final Functional Requirements pass.

Two new, nullable, purely additive columns on `newsletter_items` -- no
existing column renamed, dropped, retyped, or reinterpreted, and no CHECK
constraint anywhere in this schema needs updating (this codebase's enums
are plain VARCHAR + application-level validation, not native SQL ENUM
types -- see app/models/enums.py's module docstring):

  - `section` (nullable string): persists the Newsletter section/
    destination an editor assigns to an item (spec item 9 -- "the
    selected Newsletter section/destination must not exist only in
    browser state"). NULL for every existing row until explicitly set.

  - `published_at` (nullable timestamp): set exactly once, the moment an
    item actually reaches `published` (see
    app/services/workflows/newsletter_workflow.py::publish()). Backs the
    newest-first ordering required by `GET /newsletter/published` (spec
    item 10) without relying on `updated_at`, which can change for
    unrelated reasons after publication (e.g. a later `section` edit) and
    would silently reorder the public feed.

Both columns are additive and safe to deploy ahead of the application
code that starts writing them (old app code simply never touches them;
new app code can read them as NULL for any row published before this
migration ran -- `published_at IS NULL` is a true, honest "we don't know
when this was published" for that historical data, not corrupted state).

Downgrade drops both columns -- safe as long as no other migration has
started depending on them (none does, as of this revision).
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "newsletter_items",
        sa.Column("section", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "newsletter_items",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_newsletter_items_published_at", "newsletter_items", ["published_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_newsletter_items_published_at", table_name="newsletter_items")
    op.drop_column("newsletter_items", "published_at")
    op.drop_column("newsletter_items", "section")
