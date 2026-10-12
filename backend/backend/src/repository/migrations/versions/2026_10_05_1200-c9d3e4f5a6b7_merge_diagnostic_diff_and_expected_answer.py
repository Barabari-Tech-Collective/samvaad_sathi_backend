"""merge diagnostic_diff_check and expected_answer heads

Revision ID: c9d3e4f5a6b7
Revises: f2620f683b47, b3c4d5e6f7a8
Create Date: 2026-10-05 12:00:00.000000

Why this exists: the production server had a local-only migration
f2620f683b47 (diagnostic_diff_check) that was never pushed to the repo.
It branched from e43377ebefcd in parallel with our main chain. Stamping
f2620f683b47 on the production DB before running upgrade heads left the
DB at two concurrent heads. This no-op merge migration unifies them so
future deploys see a single head and can run 'alembic upgrade head'.
"""

from alembic import op

revision = 'c9d3e4f5a6b7'
down_revision = ('f2620f683b47', 'b3c4d5e6f7a8')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
