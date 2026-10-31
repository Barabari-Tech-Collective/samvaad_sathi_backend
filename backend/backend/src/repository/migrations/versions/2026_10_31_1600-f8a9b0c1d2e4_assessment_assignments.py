"""AI assessment assignments with availability window and pinned template (architecture pack F14)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "f8a9b0c1d2e4"
down_revision = "e7f8a9b0c1d3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "assessment_assignment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assigned_by", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("course_id", sa.String(128), nullable=False),
        sa.Column("cohort_id", sa.Integer()),
        sa.Column("job_profile_id", sa.Integer(), sa.ForeignKey("job_profile.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("pinned_revision", sa.Integer(), nullable=False),
        sa.Column("difficulty", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closes_at", sa.DateTime(timezone=True)),
        sa.Column("instructions", sa.Text()),
        sa.Column("accommodations", JSONB()),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="assigned"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("interview_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("student_id", "request_key", name="uq_assessment_assignment_request"),
        sa.CheckConstraint(
            "status IN ('assigned', 'started', 'completed', 'cancelled')",
            name="ck_assessment_assignment_status",
        ),
        sa.CheckConstraint(
            "closes_at IS NULL OR closes_at > opens_at", name="ck_assessment_assignment_window"
        ),
    )
    op.create_index("ix_assessment_assignment_student_id", "assessment_assignment", ["student_id"])
    op.create_index("ix_assessment_assignment_course_id", "assessment_assignment", ["course_id"])
    op.create_index("ix_assessment_assignment_job_profile_id", "assessment_assignment", ["job_profile_id"])


def downgrade():
    op.drop_table("assessment_assignment")
