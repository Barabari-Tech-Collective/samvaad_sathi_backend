"""add durable Practice drill completions

Revision ID: a3c4d5e6f7b8
Revises: f2a3b4c5d6e7
Create Date: 2026-10-17 06:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c4d5e6f7b8"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "practice_drill_completion",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("module", sa.String(length=32), nullable=False),
        sa.Column("level", sa.String(length=24), nullable=False),
        sa.Column("client_completion_id", sa.String(length=64), nullable=False),
        sa.Column("evidence_kind", sa.String(length=24), nullable=False),
        sa.Column("evidence_units", sa.Integer(), nullable=False),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "client_completion_id",
            name="uq_practice_drill_completion_user_client_id",
        ),
    )
    op.create_index(
        "ix_practice_drill_completion_user_completed_at",
        "practice_drill_completion",
        ["user_id", "completed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_practice_drill_completion_user_completed_at",
        table_name="practice_drill_completion",
    )
    op.drop_table("practice_drill_completion")
