"""Add optional evaluation intent to Bulk ATS batches.

Revision ID: b8d5f9a3c722
Revises: a7c4e8f2b611
Create Date: 2026-10-15 04:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d5f9a3c722"
down_revision: str | None = "a7c4e8f2b611"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bulk_ats_batch",
        sa.Column("evaluation_intent", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bulk_ats_batch", "evaluation_intent")
