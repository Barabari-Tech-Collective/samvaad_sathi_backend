"""add practice session lifecycle

Revision ID: a42f7c91d6e8
Revises: f19e4b7a2c31
Create Date: 2026-10-13 14:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "a42f7c91d6e8"
down_revision = "f19e4b7a2c31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "interview", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "interview", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_interview_paused_at", "interview", ["paused_at"], unique=False)
    op.create_index(
        "ix_interview_expires_at", "interview", ["expires_at"], unique=False
    )
    op.execute("""
        UPDATE interview
        SET expires_at = created_at + INTERVAL '7 days'
        WHERE interview_mode = 'practice'
          AND status IN ('active', 'paused')
          AND expires_at IS NULL
        """)


def downgrade() -> None:
    op.drop_index("ix_interview_expires_at", table_name="interview")
    op.drop_index("ix_interview_paused_at", table_name="interview")
    op.drop_column("interview", "expires_at")
    op.drop_column("interview", "paused_at")
