"""Merge the Practice Interview and Bulk ATS migration branches.

Revision ID: d91e6f4a2b30
Revises: c84d2f10b7a9, f1b2c3d4e5a6
Create Date: 2026-10-14 10:00:00
"""

from collections.abc import Sequence

revision: str = "d91e6f4a2b30"
down_revision: tuple[str, str] = ("c84d2f10b7a9", "f1b2c3d4e5a6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join both already-applied schema branches without changing data."""


def downgrade() -> None:
    """Split the migration graph back into its two parent heads."""
