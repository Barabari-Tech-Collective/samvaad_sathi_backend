"""External human appointments, separate from AI attempts and publications."""

from alembic import op
import sqlalchemy as sa

revision = "a5b6c7d8e9f0"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "human_interview",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.String(128), nullable=False),
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "host_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("meet_url", sa.String(100), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="scheduled"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("close_reason", sa.Text()),
        sa.Column(
            "closed_by", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL")
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "host_id", "request_key", name="uq_human_interview_request"
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'completed', 'cancelled', 'no_show', 'technical_issue')",
            name="ck_human_interview_status",
        ),
        sa.CheckConstraint(
            "ends_at > starts_at AND ends_at <= starts_at + interval '3 hours'",
            name="ck_human_interview_duration",
        ),
        sa.CheckConstraint(
            "student_id <> host_id", name="ck_human_interview_participants"
        ),
        sa.CheckConstraint("version >= 1", name="ck_human_interview_version"),
    )
    for column in ("course_id", "student_id", "host_id", "starts_at"):
        op.create_index(f"ix_human_interview_{column}", "human_interview", [column])


def downgrade():
    op.drop_table("human_interview")
