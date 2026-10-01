"""Add admin_comment to job_profile

Revision ID: 827560296990
Revises: e43377ebefcd
Create Date: 2026-09-29 00:46:37.168810

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '827560296990'
down_revision = 'e43377ebefcd'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('job_profile', sa.Column('admin_comment', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('job_profile', 'admin_comment')
