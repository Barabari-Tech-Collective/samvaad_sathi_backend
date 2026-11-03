"""Student-reported transcript and feedback issues (architecture pack F11)."""

from alembic import op
import sqlalchemy as sa

revision = "c5d6e7f8a9b1"
down_revision = "b4c5d6e7f8a0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "feedback_issue",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reporter_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("interview_id", sa.Integer(), sa.ForeignKey("interview.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_attempt_id", sa.Integer()),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("resolution", sa.Text()),
        sa.Column("resolved_by", sa.Integer()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("reporter_id", "request_key", name="uq_feedback_issue_request"),
        sa.CheckConstraint(
            "kind IN ('transcription', 'feedback', 'language', 'other')", name="ck_feedback_issue_kind"
        ),
        sa.CheckConstraint("status IN ('open', 'resolved', 'dismissed')", name="ck_feedback_issue_status"),
    )
    op.create_index("ix_feedback_issue_reporter_id", "feedback_issue", ["reporter_id"])
    op.create_index("ix_feedback_issue_interview_id", "feedback_issue", ["interview_id"])


def downgrade():
    op.drop_index("ix_feedback_issue_interview_id", "feedback_issue")
    op.drop_index("ix_feedback_issue_reporter_id", "feedback_issue")
    op.drop_table("feedback_issue")
