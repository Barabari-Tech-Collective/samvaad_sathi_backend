"""Course and batch choices students make for themselves."""

from alembic import op
import sqlalchemy as sa

revision = "c1d2e3f4a5b7"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "student_placement",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_key", sa.String(128), nullable=False),
        sa.Column("batch_name", sa.String(200), nullable=False),
        sa.Column("course_id", sa.String(128), nullable=False),
        sa.Column("course_name", sa.String(200)),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_student_placement_student_id", "student_placement", ["student_id"])
    op.create_index(
        "uq_student_placement_batch",
        "student_placement",
        ["student_id", "batch_key"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "uq_student_placement_course",
        "student_placement",
        ["student_id", "course_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )


def downgrade():
    op.drop_index("uq_student_placement_course", table_name="student_placement")
    op.drop_index("uq_student_placement_batch", table_name="student_placement")
    op.drop_index("ix_student_placement_student_id", table_name="student_placement")
    op.drop_table("student_placement")
