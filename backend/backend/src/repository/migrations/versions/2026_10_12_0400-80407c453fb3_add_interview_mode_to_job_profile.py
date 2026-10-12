"""add interview_mode to job_profile

Splits Practice from Assessment as two distinct products rather than one
dropdown mixing free AI practice with facilitator-curated, scored roles. A
facilitator now picks, at creation, whether a role's result is ever seen by
anyone (assessment) or purely the student's own practice.

Every row that exists today was created when Assessment was the only thing a
job profile could be - nothing else was ever offered - so defaulting existing
rows to "assessment" changes no one's behaviour; it only lets new roles choose
otherwise going forward.

Revision ID: 80407c453fb3
Revises: d4a9f2e81c65
Create Date: 2026-10-12 04:00:00.000000
"""

from alembic import op

revision = "80407c453fb3"
down_revision = "d4a9f2e81c65"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: this app also patches columns onto tables at startup when
    # production has not yet migrated (see a1c4e7b92f08, c2f81d6a3b47), and a
    # plain add_column there would abort this migration with DuplicateColumn
    # the moment either mechanism runs before the other.
    op.execute(
        "ALTER TABLE job_profile ADD COLUMN IF NOT EXISTS "
        "interview_mode VARCHAR(16) NOT NULL DEFAULT 'assessment'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_job_profile_interview_mode "
        "ON job_profile (interview_mode)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_job_profile_interview_mode")
    op.execute("ALTER TABLE job_profile DROP COLUMN IF EXISTS interview_mode")
