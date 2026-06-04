"""add job profile question expanded details

Revision ID: job_profile_question_details_001
Revises: job_profile_submit_001
Create Date: 2026-06-02 01:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "job_profile_question_details_001"
down_revision = "job_profile_submit_001"
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("job_profile_question")}

    if "keywords" not in existing_cols:
        op.add_column("job_profile_question", sa.Column("keywords", JSONB, nullable=True, server_default="[]"))
    if "concepts_covered" not in existing_cols:
        op.add_column("job_profile_question", sa.Column("concepts_covered", JSONB, nullable=True, server_default="[]"))
    if "expected_answer" not in existing_cols:
        op.add_column("job_profile_question", sa.Column("expected_answer", sa.Text(), nullable=True))
    if "example_output" not in existing_cols:
        op.add_column("job_profile_question", sa.Column("example_output", sa.Text(), nullable=True))

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("job_profile_question")}

    if "keywords" in existing_cols:
        op.drop_column("job_profile_question", "keywords")
    if "concepts_covered" in existing_cols:
        op.drop_column("job_profile_question", "concepts_covered")
    if "expected_answer" in existing_cols:
        op.drop_column("job_profile_question", "expected_answer")
    if "example_output" in existing_cols:
        op.drop_column("job_profile_question", "example_output")
