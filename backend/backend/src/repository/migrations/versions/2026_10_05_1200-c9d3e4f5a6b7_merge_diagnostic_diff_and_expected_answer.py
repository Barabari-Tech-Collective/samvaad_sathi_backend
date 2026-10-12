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

2026-10-12: f2620f683b47 removed from down_revision. Alembic resolves every
revision in a down_revision against a file on disk, and no file has ever
defined that one - it existed only in the production database's version
table. The chain was therefore unresolvable for everybody, and
`alembic upgrade head` failed with KeyError: 'f2620f683b47' on any clean
checkout. Nobody noticed because the project had no CI until now.

Production has since been stamped to 7af582260700 - a single row, verified -
so the ghost revision is gone from the database too and nothing refers to it
any more. This migration keeps its own id so any environment that already
applied it stays consistent.
"""

from alembic import op

revision = 'c9d3e4f5a6b7'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
