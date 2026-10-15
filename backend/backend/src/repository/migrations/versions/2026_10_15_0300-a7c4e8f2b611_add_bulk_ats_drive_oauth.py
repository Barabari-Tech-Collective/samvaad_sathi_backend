"""Add encrypted recruiter Google Drive OAuth credentials.

Revision ID: a7c4e8f2b611
Revises: f6b9d3c7a210
Create Date: 2026-10-15 03:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c4e8f2b611"
down_revision: str | None = "f6b9d3c7a210"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bulk_ats_drive_credential",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=False),
        sa.Column("google_email", sa.String(length=254), nullable=True),
        sa.Column("scopes", postgresql.JSONB(), nullable=True),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bulk_ats_drive_credential_user_id",
        "bulk_ats_drive_credential",
        ["user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("bulk_ats_drive_credential")
