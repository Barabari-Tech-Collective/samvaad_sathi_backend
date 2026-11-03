"""Store display name and email for the Bulk ATS run creator.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bulk_ats_batch",
        sa.Column("creator_name", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "bulk_ats_batch",
        sa.Column("creator_email", sa.String(length=320), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bulk_ats_batch", "creator_email")
    op.drop_column("bulk_ats_batch", "creator_name")
