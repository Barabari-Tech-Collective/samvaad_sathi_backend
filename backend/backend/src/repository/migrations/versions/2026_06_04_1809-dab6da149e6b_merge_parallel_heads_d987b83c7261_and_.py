"""merge parallel heads d987b83c7261 and job_profile_question_details_001

Revision ID: dab6da149e6b
Revises: d987b83c7261, job_profile_question_details_001
Create Date: 2026-06-04 18:09:57.652391

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'dab6da149e6b'
down_revision = ('d987b83c7261', 'job_profile_question_details_001')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
