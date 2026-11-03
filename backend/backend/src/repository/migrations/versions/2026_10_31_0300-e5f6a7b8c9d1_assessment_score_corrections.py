"""Versioned corrections to AI assessment scores (architecture pack F16)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "e5f6a7b8c9d1"
down_revision = "d4e5f6a7b8c0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "assessment_score_correction",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "interview_id",
            sa.Integer(),
            sa.ForeignKey("interview.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("knowledge_pct", sa.Integer()),
        sa.Column("speech_pct", sa.Integer(), nullable=False),
        sa.Column("previous", JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("student_note", sa.Text()),
        sa.Column("corrected_by", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("interview_id", "version", name="uq_score_correction_version"),
        sa.CheckConstraint(
            "speech_pct BETWEEN 0 AND 100 AND (knowledge_pct IS NULL OR knowledge_pct BETWEEN 0 AND 100)",
            name="ck_score_correction_range",
        ),
    )
    op.create_index(
        "ix_assessment_score_correction_interview_id",
        "assessment_score_correction",
        ["interview_id"],
    )


def downgrade():
    op.drop_index("ix_assessment_score_correction_interview_id", "assessment_score_correction")
    op.drop_table("assessment_score_correction")
