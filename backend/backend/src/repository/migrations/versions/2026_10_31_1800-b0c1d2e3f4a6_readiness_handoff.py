"""Readiness rules, opening-round assignments and company handoff."""

from alembic import op
import sqlalchemy as sa

revision = "b0c1d2e3f4a6"
down_revision = "a9b0c1d2e3f5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("assessment_assignment", sa.Column("opportunity_round_id", sa.Integer()))
    op.create_index(
        "ix_assessment_assignment_opportunity_round_id", "assessment_assignment", ["opportunity_round_id"]
    )
    op.create_table(
        "readiness_rule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.String(128), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("min_mock_interviews", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("min_average_score", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("min_drills", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("require_improving", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.Integer()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("course_id", name="uq_readiness_rule_course"),
        sa.CheckConstraint("window_days BETWEEN 7 AND 365", name="ck_readiness_window"),
        sa.CheckConstraint("min_mock_interviews BETWEEN 1 AND 20", name="ck_readiness_mocks"),
        sa.CheckConstraint("min_average_score BETWEEN 0 AND 100", name="ck_readiness_score"),
        sa.CheckConstraint("min_drills BETWEEN 0 AND 200", name="ck_readiness_drills"),
    )
    op.create_table(
        "opportunity_handoff",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("opportunity.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("selection_id", sa.Integer(), sa.ForeignKey("opportunity_selection.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="prepared"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("contact_name", sa.String(160), nullable=False),
        sa.Column("contact_email", sa.String(255)),
        sa.Column("channel", sa.String(16)),
        sa.Column("note", sa.Text()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("shared_by", sa.Integer()),
        sa.Column("shared_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("selection_id", name="uq_handoff_selection"),
        sa.CheckConstraint("status IN ('prepared', 'shared')", name="ck_handoff_status"),
        sa.CheckConstraint(
            "channel IS NULL OR channel IN ('email', 'portal', 'meeting', 'other')", name="ck_handoff_channel"
        ),
    )
    op.create_index("ix_opportunity_handoff_opportunity_id", "opportunity_handoff", ["opportunity_id"])
    op.create_table(
        "opportunity_handoff_candidate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("handoff_id", sa.Integer(), sa.ForeignKey("opportunity_handoff.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("company_status", sa.String(16), nullable=False, server_default="not_sent"),
        sa.Column("status_note", sa.Text()),
        sa.Column("updated_by", sa.Integer()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("handoff_id", "student_id", name="uq_handoff_candidate"),
        sa.CheckConstraint(
            "company_status IN ('not_sent', 'sent', 'interviewing', 'offered', 'joined', 'declined', 'not_selected')",
            name="ck_handoff_company_status",
        ),
    )
    op.create_index("ix_opportunity_handoff_candidate_handoff_id", "opportunity_handoff_candidate", ["handoff_id"])


def downgrade():
    op.drop_table("opportunity_handoff_candidate")
    op.drop_table("opportunity_handoff")
    op.drop_table("readiness_rule")
    op.drop_index("ix_assessment_assignment_opportunity_round_id", "assessment_assignment")
    op.drop_column("assessment_assignment", "opportunity_round_id")
