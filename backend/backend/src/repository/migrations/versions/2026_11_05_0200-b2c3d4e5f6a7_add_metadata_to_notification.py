"""Add metadata JSONB column to notification table.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-11-05 02:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE notification ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;"
    )


def downgrade():
    op.execute("ALTER TABLE notification DROP COLUMN IF EXISTS metadata;")

