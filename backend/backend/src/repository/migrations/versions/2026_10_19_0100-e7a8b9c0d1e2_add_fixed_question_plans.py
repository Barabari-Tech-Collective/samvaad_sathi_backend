"""Pin fixed-length question plans without changing legacy attempts."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e7a8b9c0d1e2"
down_revision = "d6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "job_profile", sa.Column("questions_per_interview", sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        "ck_job_profile_sitting_count",
        "job_profile",
        "questions_per_interview IS NULL OR questions_per_interview BETWEEN 1 AND 50",
    )
    op.add_column(
        "interview", sa.Column("question_plan", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "interview", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "interview",
        sa.Column("answer_deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "interview", sa.Column("submission_receipt", postgresql.JSONB(), nullable=True)
    )


def downgrade():
    op.drop_column("interview", "submission_receipt")
    op.drop_column("interview", "answer_deadline_at")
    op.drop_column("interview", "started_at")
    op.drop_column("interview", "question_plan")
    op.drop_constraint("ck_job_profile_sitting_count", "job_profile", type_="check")
    op.drop_column("job_profile", "questions_per_interview")
