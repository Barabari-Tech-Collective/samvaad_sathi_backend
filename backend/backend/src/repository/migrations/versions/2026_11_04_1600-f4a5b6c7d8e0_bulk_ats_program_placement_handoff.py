"""Add program to placement handoff lifecycle to Bulk ATS candidates.

Revision ID: f4a5b6c7d8e0
Revises: e3f4a5b6c7d9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4a5b6c7d8e0"
down_revision: str | None = "e3f4a5b6c7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bulk_ats_candidate",
        sa.Column("handoff_status", sa.String(50), nullable=False, server_default="screened"),
    )
    op.add_column(
        "bulk_ats_candidate",
        sa.Column("program_readiness_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "bulk_ats_candidate",
        sa.Column("program_feedback_notes", sa.Text(), nullable=True),
    )
    op.add_column(
        "bulk_ats_candidate",
        sa.Column("program_reviewed_by", sa.Integer(), nullable=True),
    )
    op.add_column(
        "bulk_ats_candidate",
        sa.Column("program_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bulk_ats_candidate",
        sa.Column("placement_notes", sa.Text(), nullable=True),
    )
    op.add_column(
        "bulk_ats_candidate",
        sa.Column(
            "placement_assigned_companies",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_foreign_key(
        "fk_bulk_ats_candidate_program_reviewed_by",
        "bulk_ats_candidate",
        "user",
        ["program_reviewed_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_bulk_ats_candidate_handoff_status",
        "bulk_ats_candidate",
        ["handoff_status"],
    )
    op.create_check_constraint(
        "chk_bulk_ats_candidate_handoff_status",
        "bulk_ats_candidate",
        "handoff_status IN ('screened', 'in_program_review', 'approved_for_placement', 'shared_with_placement', 'accepted_in_placement', 'rejected', 'needs_info', 'referred_to_hr', 'archived')",
    )
    op.create_check_constraint(
        "chk_bulk_ats_candidate_readiness_score",
        "bulk_ats_candidate",
        "program_readiness_score IS NULL OR (program_readiness_score >= 1 AND program_readiness_score <= 10)",
    )


def downgrade() -> None:
    op.drop_constraint("chk_bulk_ats_candidate_readiness_score", "bulk_ats_candidate", type_="check")
    op.drop_constraint("chk_bulk_ats_candidate_handoff_status", "bulk_ats_candidate", type_="check")
    op.drop_index("ix_bulk_ats_candidate_handoff_status", table_name="bulk_ats_candidate")
    op.drop_constraint(
        "fk_bulk_ats_candidate_program_reviewed_by",
        "bulk_ats_candidate",
        type_="foreignkey",
    )
    op.drop_column("bulk_ats_candidate", "placement_assigned_companies")
    op.drop_column("bulk_ats_candidate", "placement_notes")
    op.drop_column("bulk_ats_candidate", "program_reviewed_at")
    op.drop_column("bulk_ats_candidate", "program_reviewed_by")
    op.drop_column("bulk_ats_candidate", "program_feedback_notes")
    op.drop_column("bulk_ats_candidate", "program_readiness_score")
    op.drop_column("bulk_ats_candidate", "handoff_status")

