"""Add safe canonical duplicate merging for Bulk ATS candidates.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bulk_ats_candidate", sa.Column("merged_into_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_bulk_ats_candidate_merged_into",
        "bulk_ats_candidate",
        "bulk_ats_candidate",
        ["merged_into_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_bulk_ats_candidate_merged_into_id", "bulk_ats_candidate", ["merged_into_id"])


def downgrade() -> None:
    op.drop_index("ix_bulk_ats_candidate_merged_into_id", table_name="bulk_ats_candidate")
    op.drop_constraint("fk_bulk_ats_candidate_merged_into", "bulk_ats_candidate", type_="foreignkey")
    op.drop_column("bulk_ats_candidate", "merged_into_id")
