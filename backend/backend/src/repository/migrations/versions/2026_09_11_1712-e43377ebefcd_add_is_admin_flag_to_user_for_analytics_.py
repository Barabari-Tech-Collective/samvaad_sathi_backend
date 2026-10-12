"""add is_admin flag to user for analytics authorization

Revision ID: e43377ebefcd
Revises: 5958de3fd475
Create Date: 2026-09-11 17:12:36.082042

"""
from alembic import op


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
    # Startup patches this column in too - it was added there after a deploy
    # without this migration broke login on staging - so a bare add_column
    # would abort here with DuplicateColumn on any host that had already
    # booted. Whichever mechanism runs first, the other must be a no-op.
    op.execute(
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS is_admin '
        "BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute('ALTER TABLE "user" DROP COLUMN IF EXISTS is_admin')
