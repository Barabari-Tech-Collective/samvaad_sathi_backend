"""add interview session lease

Revision ID: c84d2f10b7a9
Revises: a42f7c91d6e8
Create Date: 2026-10-13 15:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "c84d2f10b7a9"
down_revision = "a42f7c91d6e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "interview",
        sa.Column("session_lease_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "interview",
        sa.Column(
            "session_lease_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "interview",
        sa.Column(
            "session_lease_acquired_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "interview",
        sa.Column(
            "session_lease_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_index(
        "ix_interview_session_lease_expires_at",
        "interview",
        ["session_lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_interview_session_lease_expires_at", table_name="interview")
    op.drop_column("interview", "session_lease_expires_at")
    op.drop_column("interview", "session_lease_acquired_at")
    op.drop_column("interview", "session_lease_required")
    op.drop_column("interview", "session_lease_hash")
