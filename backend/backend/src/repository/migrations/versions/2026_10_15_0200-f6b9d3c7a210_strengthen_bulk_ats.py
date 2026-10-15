"""Strengthen Bulk ATS evidence, review outcomes, privacy and auditing.

Revision ID: f6b9d3c7a210
Revises: e7a4c2d9b105
Create Date: 2026-10-15 02:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6b9d3c7a210"
down_revision: str | None = "e7a4c2d9b105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bulk_ats_batch", sa.Column("jd_analysis", postgresql.JSONB(), nullable=True))
    op.add_column("bulk_ats_batch", sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_bulk_ats_batch_retention_until", "bulk_ats_batch", ["retention_until"])

    op.add_column("bulk_ats_candidate", sa.Column("confidence_score", sa.Float(), nullable=True))
    op.add_column("bulk_ats_candidate", sa.Column("score_lower_bound", sa.Float(), nullable=True))
    op.add_column("bulk_ats_candidate", sa.Column("score_upper_bound", sa.Float(), nullable=True))
    op.add_column("bulk_ats_candidate", sa.Column("reviewer_decision", sa.String(length=24), nullable=True))
    op.add_column("bulk_ats_candidate", sa.Column("decision_reason", sa.Text(), nullable=True))
    op.add_column("bulk_ats_candidate", sa.Column("interview_outcome", sa.String(length=24), nullable=True))
    op.add_column("bulk_ats_candidate", sa.Column("outcome_recorded_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "chk_bulk_ats_candidate_reviewer_decision",
        "bulk_ats_candidate",
        "reviewer_decision IS NULL OR reviewer_decision IN ('shortlist', 'hold', 'not_shortlisted')",
    )
    op.create_check_constraint(
        "chk_bulk_ats_candidate_interview_outcome",
        "bulk_ats_candidate",
        "interview_outcome IS NULL OR interview_outcome IN "
        "('not_interviewed', 'interviewed', 'advanced', 'rejected', 'hired', 'withdrew')",
    )

    op.create_table(
        "bulk_ats_audit_event",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=True),
        sa.Column("candidate_id", sa.Integer(), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["bulk_ats_batch.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["candidate_id"], ["bulk_ats_candidate.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bulk_ats_audit_event_batch_id", "bulk_ats_audit_event", ["batch_id"])
    op.create_index("ix_bulk_ats_audit_event_candidate_id", "bulk_ats_audit_event", ["candidate_id"])
    op.create_index("ix_bulk_ats_audit_event_actor_id", "bulk_ats_audit_event", ["actor_id"])
    op.create_index("ix_bulk_ats_audit_event_action", "bulk_ats_audit_event", ["action"])
    op.create_index("ix_bulk_ats_audit_event_created_at", "bulk_ats_audit_event", ["created_at"])


def downgrade() -> None:
    op.drop_table("bulk_ats_audit_event")
    op.drop_constraint("chk_bulk_ats_candidate_interview_outcome", "bulk_ats_candidate", type_="check")
    op.drop_constraint("chk_bulk_ats_candidate_reviewer_decision", "bulk_ats_candidate", type_="check")
    op.drop_column("bulk_ats_candidate", "outcome_recorded_at")
    op.drop_column("bulk_ats_candidate", "interview_outcome")
    op.drop_column("bulk_ats_candidate", "decision_reason")
    op.drop_column("bulk_ats_candidate", "reviewer_decision")
    op.drop_column("bulk_ats_candidate", "score_upper_bound")
    op.drop_column("bulk_ats_candidate", "score_lower_bound")
    op.drop_column("bulk_ats_candidate", "confidence_score")
    op.drop_index("ix_bulk_ats_batch_retention_until", table_name="bulk_ats_batch")
    op.drop_column("bulk_ats_batch", "retention_until")
    op.drop_column("bulk_ats_batch", "jd_analysis")
