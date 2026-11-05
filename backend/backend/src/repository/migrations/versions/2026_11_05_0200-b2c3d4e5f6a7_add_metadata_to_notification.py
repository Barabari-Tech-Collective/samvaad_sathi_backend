"""Add metadata JSONB column to notification table.

Revision ID: b2c3d4e5f6a7
Revises: f4a5b6c7d8e0
Create Date: 2026-11-05 02:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b2c3d4e5f6a7"
down_revision = "f4a5b6c7d8e0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "notification",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade():
    op.drop_column("notification", "metadata")
