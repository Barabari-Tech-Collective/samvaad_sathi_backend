"""add expected_answer to interview_question

Revision ID: b3c4d5e6f7a8
Revises: 7af582260700
Create Date: 2026-10-05 10:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b3c4d5e6f7a8'
down_revision = '7af582260700'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS rather than op.add_column. Production's schema was built by
    # create_all from the models, not by this chain, so the column this migration
    # introduces is very likely already there - and a bare add_column would abort
    # the deploy with DuplicateColumn. This is the first unapplied migration past
    # the revision production is stamped at, so it is the one that would actually
    # have run and failed.
    op.execute(
        "ALTER TABLE interview_question ADD COLUMN IF NOT EXISTS expected_answer TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE interview_question DROP COLUMN IF EXISTS expected_answer")
