"""Add reviewer assignment for Bulk ATS candidates.

Revision ID: c3d4e5f6a7b8
Revises: e1f2a3b4c5d6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bulk_ats_candidate",
        sa.Column("assigned_reviewer_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_bulk_ats_candidate_assigned_reviewer",
        "bulk_ats_candidate",
        "user",
        ["assigned_reviewer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_bulk_ats_candidate_assigned_reviewer_id",
        "bulk_ats_candidate",
        ["assigned_reviewer_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_bulk_ats_candidate_assigned_reviewer_id", table_name="bulk_ats_candidate")
    op.drop_constraint(
        "fk_bulk_ats_candidate_assigned_reviewer",
        "bulk_ats_candidate",
        type_="foreignkey",
    )
    op.drop_column("bulk_ats_candidate", "assigned_reviewer_id")
