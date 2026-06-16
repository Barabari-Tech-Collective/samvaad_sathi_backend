"""add job profile category and employment type

Revision ID: job_profile_metadata_001
Revises: job_profile_question_001
Create Date: 2026-06-01 02:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "job_profile_metadata_001"
down_revision = "job_profile_question_001"
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("job_profile")}

    if "category" not in existing_cols:
        op.add_column("job_profile", sa.Column("category", sa.String(length=160), nullable=True))
    if "employment_type" not in existing_cols:
        op.add_column("job_profile", sa.Column("employment_type", sa.String(length=64), nullable=True))

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("job_profile")}

    if "category" in existing_cols:
        op.drop_column("job_profile", "category")
    if "employment_type" in existing_cols:
        op.drop_column("job_profile", "employment_type")
