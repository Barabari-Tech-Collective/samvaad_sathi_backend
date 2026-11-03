"""Cohorts: staff-managed groups of learners inside a course (architecture pack sec 4)."""

from alembic import op
import sqlalchemy as sa

revision = "f6a7b8c9d0e2"
down_revision = "e5f6a7b8c9d1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cohort",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Integer()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("course_id", "request_key", name="uq_cohort_request"),
    )
    op.create_index("ix_cohort_course_id", "cohort", ["course_id"])
    op.create_table(
        "cohort_member",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "cohort_id", sa.Integer(), sa.ForeignKey("cohort.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "student_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("added_by", sa.Integer()),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("cohort_id", "student_id", name="uq_cohort_member"),
    )
    op.create_index("ix_cohort_member_cohort_id", "cohort_member", ["cohort_id"])
    op.create_index("ix_cohort_member_student_id", "cohort_member", ["student_id"])


def downgrade():
    op.drop_index("ix_cohort_member_student_id", "cohort_member")
    op.drop_index("ix_cohort_member_cohort_id", "cohort_member")
    op.drop_table("cohort_member")
    op.drop_index("ix_cohort_course_id", "cohort")
    op.drop_table("cohort")
