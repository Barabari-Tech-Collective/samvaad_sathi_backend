"""backfill role-created interviews as assessments

Interviews started from a staff-created role were landing in Practice: the
generic create endpoints take only a track name, so they never carried a
job_profile_id and snapshotted the column default, "practice". Anything that
came from a role - linked to a job profile, or carrying the role flow's
"Non-Tech:" track - was an assessment before the two modes were split, so it
is moved back. Free practice (no profile, ordinary track) is left alone.

Practice sessions expire; assessments do not, so the expiry set on the wrongly
classified rows is cleared as well.

Revision ID: d7e8f9a0b1c2
Revises: b0c1d2e3f4a6
Create Date: 2026-11-04 01:00:00.000000
"""

from alembic import op

revision = "d7e8f9a0b1c2"
down_revision = "b0c1d2e3f4a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE interview SET interview_mode = 'assessment', expires_at = NULL "
        "WHERE interview_mode = 'practice' "
        "AND (job_profile_id IS NOT NULL OR track LIKE 'Non-Tech:%')"
    )


def downgrade() -> None:
    # Not reversible: the rows that were moved are indistinguishable afterwards.
    pass
