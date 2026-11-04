"""Mock interview assignments: a teacher asks a learner to take one mock interview.

A separate table rather than a new kind of speaking drill, so that an application rollback
(which keeps the database) never shows these to the current student app as a drill."""

from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f3"
down_revision = "f6a7b8c9d0e2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mock_interview_assignment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assigned_by", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("course_id", sa.String(128), nullable=False),
        sa.Column(
            "job_profile_id", sa.Integer(), sa.ForeignKey("job_profile.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("due_date", sa.Date()),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="assigned"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("interview_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("assigned_by", "request_key", name="uq_mock_assignment_request"),
        sa.CheckConstraint("status IN ('assigned', 'completed', 'cancelled')", name="ck_mock_assignment_status"),
    )
    op.create_index("ix_mock_interview_assignment_student_id", "mock_interview_assignment", ["student_id"])
    op.create_index("ix_mock_interview_assignment_course_id", "mock_interview_assignment", ["course_id"])


def downgrade():
    op.drop_index("ix_mock_interview_assignment_course_id", "mock_interview_assignment")
    op.drop_index("ix_mock_interview_assignment_student_id", "mock_interview_assignment")
    op.drop_table("mock_interview_assignment")
