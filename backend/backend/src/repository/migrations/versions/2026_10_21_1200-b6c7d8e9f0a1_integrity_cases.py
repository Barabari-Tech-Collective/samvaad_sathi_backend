"""Add assessment human review cases without altering interview scores."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b6c7d8e9f0a1"
down_revision = "a5b6c7d8e9f0"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "integrity_case",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "interview_id",
            sa.Integer(),
            sa.ForeignKey("interview.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("course_id", sa.String(128), nullable=False),
        sa.Column("college", sa.String(512)),
        sa.Column(
            "opened_by",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), server_default="open", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("outcome", sa.String(24)),
        sa.Column(
            "decided_by", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT")
        ),
        sa.Column("appeal_deadline", sa.DateTime(timezone=True)),
        sa.Column("history", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("interview_id", name="uq_integrity_case_interview"),
        sa.CheckConstraint(
            "state IN ('open','decided','appealed','resolved')",
            name="ck_integrity_case_state",
        ),
        sa.CheckConstraint("version >= 1", name="ck_integrity_case_version"),
    )
    for key in ("interview_id", "student_id", "course_id"):
        op.create_index(f"ix_integrity_case_{key}", "integrity_case", [key])


def downgrade():
    op.drop_table("integrity_case")
