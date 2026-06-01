"""add job profile status and submitted at

Revision ID: job_profile_submit_001
Revises: job_profile_metadata_001
Create Date: 2026-06-01 03:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "job_profile_submit_001"
down_revision = "job_profile_metadata_001"
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("job_profile")}

    if "status" not in existing_cols:
        op.add_column("job_profile", sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"))
    if "submitted_at" not in existing_cols:
        op.add_column("job_profile", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("job_profile")}

    if "status" in existing_cols:
        op.drop_column("job_profile", "status")
    if "submitted_at" in existing_cols:
        op.drop_column("job_profile", "submitted_at")
