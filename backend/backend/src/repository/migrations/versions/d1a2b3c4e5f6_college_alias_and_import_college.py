"""College name clean-up and import-sheet colleges.

Revision ID: d1a2b3c4e5f6
Revises: b2c3d4e5f6a7
"""

import sqlalchemy as sa
from alembic import op

revision = "d1a2b3c4e5f6"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "college_alias",
        sa.Column("spelling_key", sa.String(256), primary_key=True),
        sa.Column("canonical_key", sa.String(256), nullable=False),
        sa.Column("canonical_label", sa.String(256), nullable=False),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_college_alias_canonical_key", "college_alias", ["canonical_key"])
    op.create_table(
        "learner_import_college",
        sa.Column("email", sa.String(320), primary_key=True),
        sa.Column("college", sa.String(256), nullable=False),
        sa.Column("added_by", sa.Integer, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("learner_import_college")
    op.drop_index("ix_college_alias_canonical_key", table_name="college_alias")
    op.drop_table("college_alias")
