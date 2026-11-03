"""Merge template-course and question-generation migration heads.

Both feature branches extend the same readiness-handoff revision.  The merge
contains no schema operation itself: Alembic applies whichever parent branch is
missing, then records this single shared head.
"""

from alembic import op  # noqa: F401 - required by Alembic migration modules


revision = "e3f4a5b6c7d9"
down_revision = ("d2e3f4a5b6c8", "c1d2e3f4a5b6")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
