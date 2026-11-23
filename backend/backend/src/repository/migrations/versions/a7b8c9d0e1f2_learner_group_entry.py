"""Colleges, courses and batches that staff add by hand.

Revision ID: a7b8c9d0e1f2
Revises: f3c4d5e6a7b8
"""

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f3c4d5e6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learner_group_entry",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("group_key", sa.String(256), nullable=False),
        sa.Column("label", sa.String(256), nullable=False),
        sa.Column("course_key", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("kind", "group_key", "course_key", name="uq_learner_group_entry"),
    )


def downgrade() -> None:
    op.drop_table("learner_group_entry")
