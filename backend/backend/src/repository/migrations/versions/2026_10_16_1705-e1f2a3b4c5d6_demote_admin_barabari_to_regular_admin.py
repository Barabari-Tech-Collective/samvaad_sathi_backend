"""Ensure admin@barabari.org is standard admin and not super_admin.

Revision ID: e1f2a3b4c5d6
Revises: b8d5f9a3c722
Create Date: 2026-10-16 17:05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "b8d5f9a3c722"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Ensure admin@barabari.org is regular admin (is_super_admin=False),
    # preserving super admin privileges exclusively for Superadmin@barabari.org.
    op.execute("UPDATE \"user\" SET is_super_admin = FALSE WHERE LOWER(email) = 'admin@barabari.org'")


def downgrade() -> None:
    pass
