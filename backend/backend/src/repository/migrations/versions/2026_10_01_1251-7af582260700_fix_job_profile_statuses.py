"""fix job profile statuses

Revision ID: 7af582260700
Revises: 827560296990
Create Date: 2026-10-01 12:51:52.488474

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7af582260700'
down_revision = '827560296990'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE job_profile SET status = 'published' WHERE status = 'approved'")
    op.execute("UPDATE job_profile SET status = 'under_review' WHERE status = 'Under Review'")
    op.execute("UPDATE job_profile SET status = 'draft' WHERE status = 'DRAFT'")


def downgrade() -> None:
    pass
