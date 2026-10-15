"""merge_teams_rbac_and_bulk_ats

Revision ID: c50d31efe73c
Revises: 7ddc54281fe2, f1b2c3d4e5a6
Create Date: 2026-10-15 12:21:31.751930

"""
from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401


# revision identifiers, used by Alembic.
revision = 'c50d31efe73c'
down_revision = ('7ddc54281fe2', 'f1b2c3d4e5a6')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
