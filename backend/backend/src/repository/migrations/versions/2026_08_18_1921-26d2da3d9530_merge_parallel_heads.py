"""merge parallel heads

Revision ID: 26d2da3d9530
Revises: 988853ec372d, 9889ca43eed1
Create Date: 2026-08-18 19:21:58.375800

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '26d2da3d9530'
down_revision = ('988853ec372d', '9889ca43eed1')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
