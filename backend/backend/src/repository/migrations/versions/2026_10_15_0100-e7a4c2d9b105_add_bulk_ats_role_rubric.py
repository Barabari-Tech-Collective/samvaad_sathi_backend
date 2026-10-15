"""Add the role rubric required for meaningful Bulk ATS scoring.

Revision ID: e7a4c2d9b105
Revises: d91e6f4a2b30
Create Date: 2026-10-15 01:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7a4c2d9b105"
down_revision: str | None = "d91e6f4a2b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bulk_ats_batch", sa.Column("role_description", sa.Text(), nullable=True))
    op.add_column("bulk_ats_batch", sa.Column("required_skills", postgresql.JSONB(), nullable=True))
    op.add_column("bulk_ats_batch", sa.Column("preferred_skills", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("bulk_ats_batch", "preferred_skills")
    op.drop_column("bulk_ats_batch", "required_skills")
    op.drop_column("bulk_ats_batch", "role_description")
