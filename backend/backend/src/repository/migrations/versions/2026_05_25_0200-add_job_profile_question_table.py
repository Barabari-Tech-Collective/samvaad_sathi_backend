"""add job profile question table

Revision ID: job_profile_question_001
Revises: job_profile_001
Create Date: 2026-05-25 02:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "job_profile_question_001"
down_revision = "job_profile_001"
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())

    if "job_profile_question" not in table_names:
        op.create_table(
            "job_profile_question",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("job_profile_id", sa.Integer(), nullable=False),
            sa.Column("question_text", sa.Text(), nullable=False),
            sa.Column("level", sa.Integer(), nullable=False),
            sa.Column("difficulty", sa.String(length=32), nullable=False),
            sa.Column("question_type", sa.String(length=64), server_default="theoretical", nullable=False),
            sa.Column("is_ai_generated", sa.Boolean(), server_default=sa.text("true"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
            sa.ForeignKeyConstraint(["job_profile_id"], ["job_profile.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    
    # Indexes
    inspector = inspect(bind)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("job_profile_question")}
    
    idx_job_profile_id = "ix_job_profile_question_job_profile_id"
    idx_level = "ix_job_profile_question_level"
    idx_difficulty = "ix_job_profile_question_difficulty"

    if idx_job_profile_id not in existing_indexes:
        op.create_index(idx_job_profile_id, "job_profile_question", ["job_profile_id"], unique=False)
    if idx_level not in existing_indexes:
        op.create_index(idx_level, "job_profile_question", ["level"], unique=False)
    if idx_difficulty not in existing_indexes:
        op.create_index(idx_difficulty, "job_profile_question", ["difficulty"], unique=False)

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())

    if "job_profile_question" not in table_names:
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("job_profile_question")}
    for idx_name in (
        "ix_job_profile_question_job_profile_id",
        "ix_job_profile_question_level",
        "ix_job_profile_question_difficulty",
    ):
        if idx_name in existing_indexes:
            op.drop_index(idx_name, table_name="job_profile_question")

    op.drop_table("job_profile_question")
