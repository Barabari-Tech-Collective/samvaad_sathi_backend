"""Add course-bound coaching assignments without attributing legacy rows."""

from alembic import op
import sqlalchemy as sa

revision = "e3f4a5b6c7d8"
down_revision = "d2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "coaching_assignment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assigned_by",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("course_id", sa.String(128), nullable=False),
        sa.Column("module", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("due_date", sa.Date()),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="assigned"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "completion_id",
            sa.Integer(),
            sa.ForeignKey("practice_drill_completion.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "assigned_by", "request_key", name="uq_coaching_assignment_request"
        ),
        sa.CheckConstraint(
            "status IN ('assigned', 'completed', 'cancelled')",
            name="ck_coaching_assignment_status",
        ),
        sa.CheckConstraint(
            "module IN ('warm_up', 'think_fast')", name="ck_coaching_assignment_module"
        ),
        sa.CheckConstraint("version >= 1", name="ck_coaching_assignment_version"),
    )
    op.create_index(
        "ix_coaching_assignment_student_id", "coaching_assignment", ["student_id"]
    )
    op.create_index(
        "ix_coaching_assignment_course_id", "coaching_assignment", ["course_id"]
    )


def downgrade():
    op.drop_table("coaching_assignment")
