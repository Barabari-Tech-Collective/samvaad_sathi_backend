"""Human interview scorecards and an assigned interviewer (architecture pack F17, F20)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "f2a3b4c5d6e8"
down_revision = "e1f2a3b4c5d7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("human_interview", sa.Column("interviewer_id", sa.Integer()))
    op.create_index("ix_human_interview_interviewer_id", "human_interview", ["interviewer_id"])
    op.create_table(
        "human_scorecard",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "appointment_id",
            sa.Integer(),
            sa.ForeignKey("human_interview.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("criteria", JSONB(), nullable=False, server_default="[]"),
        sa.Column("suggested_result", sa.String(24)),
        sa.Column("internal_notes", sa.Text()),
        sa.Column("student_comments", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("submitted_by", sa.Integer()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.Integer()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'approved', 'returned')",
            name="ck_human_scorecard_status",
        ),
    )


def downgrade():
    op.drop_table("human_scorecard")
    op.drop_index("ix_human_interview_interviewer_id", "human_interview")
    op.drop_column("human_interview", "interviewer_id")
