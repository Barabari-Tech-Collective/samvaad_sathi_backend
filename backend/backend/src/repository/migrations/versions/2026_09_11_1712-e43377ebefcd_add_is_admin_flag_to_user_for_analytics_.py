"""add is_admin flag to user for analytics authorization

Revision ID: e43377ebefcd
Revises: 5958de3fd475
Create Date: 2026-09-11 17:12:36.082042

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e43377ebefcd'
down_revision = '5958de3fd475'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Defaults to false for every existing row: previously ANY authenticated
    # user could read the cross-student analytics endpoints, so the safe
    # migration is to revoke from everyone and re-grant deliberately.
    # After deploying, grant the real mentor/ops accounts with e.g.:
    #   UPDATE "user" SET is_admin = true WHERE email IN ('ops@example.org');
    op.add_column(
        "user",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("user", "is_admin")
