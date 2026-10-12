"""add proctoring fields to interview

proctoring_terminated and proctoring_violation_reason record when an
Assessment interview was ended early because the student left the tab or
exited fullscreen. proctoring_terminated=True is itself the facilitator-
visible remark; completed_at (already a column) doubles as when it happened,
so no new timestamp column is needed for a fact the status change already
dates.

Revision ID: 9b6ce6018b3f
Revises: c8a58ef421ba
Create Date: 2026-10-12 06:00:00.000000
"""

from alembic import op

revision = "9b6ce6018b3f"
down_revision = "c8a58ef421ba"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: this app also patches columns onto tables at startup when
    # production has not yet migrated - see a1c4e7b92f08, c2f81d6a3b47 - and a
    # plain add_column there would abort this migration with DuplicateColumn
    # the moment either mechanism runs before the other.
    op.execute(
        "ALTER TABLE interview ADD COLUMN IF NOT EXISTS "
        "proctoring_terminated BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE interview ADD COLUMN IF NOT EXISTS "
        "proctoring_violation_reason VARCHAR(32)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_interview_proctoring_terminated "
        "ON interview (proctoring_terminated)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_interview_proctoring_terminated")
    op.execute("ALTER TABLE interview DROP COLUMN IF EXISTS proctoring_violation_reason")
    op.execute("ALTER TABLE interview DROP COLUMN IF EXISTS proctoring_terminated")
