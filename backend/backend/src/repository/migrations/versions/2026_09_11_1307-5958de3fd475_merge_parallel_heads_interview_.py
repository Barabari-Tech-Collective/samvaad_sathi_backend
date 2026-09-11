"""merge parallel heads: interview composite indexes and pronunciation practice tables

Revision ID: 5958de3fd475
Revises: 7239ec166ac3, f04f4122dd5b
Create Date: 2026-09-11 13:07:34.373316

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5958de3fd475'
down_revision = ('7239ec166ac3', 'f04f4122dd5b')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
