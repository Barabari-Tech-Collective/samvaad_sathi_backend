"""Store immutable Bulk ATS rubric context per batch.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bulk_ats_batch", sa.Column("rubric_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("bulk_ats_batch", sa.Column("rubric_snapshot", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("bulk_ats_batch", "rubric_snapshot")
    op.drop_column("bulk_ats_batch", "rubric_version")
