"""All five drills can be assigned; Program to Placement questions and pitch delivery are recorded.

Revision ID: e2b3c4d5f6a7
Revises: d1a2b3c4e5f6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2b3c4d5f6a7"
down_revision: str | None = "d1a2b3c4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_coaching_assignment_module", "coaching_assignment", type_="check")
    op.create_check_constraint(
        "ck_coaching_assignment_module",
        "coaching_assignment",
        "module IN ('warm_up', 'think_fast', 'pronunciation', 'structure', 'pace')",
    )
    op.add_column("coaching_assignment", sa.Column("evidence_kind", sa.String(24), nullable=True))
    op.add_column("coaching_assignment", sa.Column("evidence_id", sa.Integer(), nullable=True))
    op.create_index(
        "uq_coaching_assignment_evidence",
        "coaching_assignment",
        ["evidence_kind", "evidence_id"],
        unique=True,
        postgresql_where=sa.text("evidence_id IS NOT NULL"),
    )
    op.create_table(
        "bulk_ats_handoff_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("bulk_ats_candidate.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('shared', 'question', 'reply', 'accepted', 'declined', 'moved_back', 'pitched', 'pitch_failed', 'note')",
            name="ck_bulk_ats_handoff_message_kind",
        ),
    )
    op.create_table(
        "bulk_ats_pitch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("bulk_ats_candidate.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("hr_name", sa.String(255), nullable=False),
        sa.Column("hr_email", sa.String(255), nullable=False),
        sa.Column("custom_notes", sa.Text(), nullable=True),
        sa.Column("include_resume_links", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("delivered", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("sent_by", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("bulk_ats_candidate", sa.Column("placement_decided_by", sa.Integer(), nullable=True))
    op.add_column("bulk_ats_candidate", sa.Column("placement_decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bulk_ats_candidate", sa.Column("handoff_shared_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for name in ("handoff_shared_at", "placement_decided_at", "placement_decided_by"):
        op.drop_column("bulk_ats_candidate", name)
    op.drop_table("bulk_ats_pitch")
    op.drop_table("bulk_ats_handoff_message")
    op.drop_index("uq_coaching_assignment_evidence", table_name="coaching_assignment")
    op.drop_column("coaching_assignment", "evidence_id")
    op.drop_column("coaching_assignment", "evidence_kind")
    op.drop_constraint("ck_coaching_assignment_module", "coaching_assignment", type_="check")
    op.create_check_constraint(
        "ck_coaching_assignment_module", "coaching_assignment", "module IN ('warm_up', 'think_fast')"
    )
