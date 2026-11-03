"""Persist ordered pronunciation-word interactions before a session can complete."""

from alembic import op
import sqlalchemy as sa


revision = "e1f2a3b4c5d7"
down_revision = "d0e1f2a3b4c6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "pronunciation_practice",
        sa.Column("completed_word_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("pronunciation_practice", "completed_word_count")
