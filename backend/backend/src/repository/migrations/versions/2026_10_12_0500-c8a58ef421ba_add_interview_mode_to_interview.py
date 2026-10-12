"""add interview_mode to interview

A snapshot of the job profile's interview_mode at the moment the interview was
created, not a live read of it. job_profile_id goes NULL if the profile is
later deleted (ON DELETE SET NULL), and a facilitator can edit a published
profile's mode at any time - without this column, either event retroactively
reclassifies every interview already sat against that role, in history and in
analytics, after the fact.

Existing rows backfill in two steps, because "practice" is only the right
default for interviews that were never tied to a job profile at all - free
generation is practice by definition. Any interview that does carry a
job_profile_id was created when Assessment was the only thing a job profile
could produce, so those backfill to "assessment" explicitly, overriding the
column default.

Revision ID: c8a58ef421ba
Revises: 80407c453fb3
Create Date: 2026-10-12 05:00:00.000000
"""

from alembic import op

revision = "c8a58ef421ba"
down_revision = "80407c453fb3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: this app also patches columns onto tables at startup when
    # production has not yet migrated - see a1c4e7b92f08, c2f81d6a3b47 - and a
    # plain add_column there would abort this migration with DuplicateColumn
    # the moment either mechanism runs before the other.
    op.execute(
        "ALTER TABLE interview ADD COLUMN IF NOT EXISTS "
        "interview_mode VARCHAR(16) NOT NULL DEFAULT 'practice'"
    )
    op.execute(
        "UPDATE interview SET interview_mode = 'assessment' WHERE job_profile_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_interview_interview_mode ON interview (interview_mode)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_interview_interview_mode")
    op.execute("ALTER TABLE interview DROP COLUMN IF EXISTS interview_mode")
