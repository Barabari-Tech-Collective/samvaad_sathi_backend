"""add expected_answer to interview_question

Revision ID: b3c4d5e6f7a8
Revises: 7af582260700
Create Date: 2026-10-05 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3c4d5e6f7a8'
down_revision = '7af582260700'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('interview_question', sa.Column('expected_answer', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('interview_question', 'expected_answer')
