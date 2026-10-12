"""merge the transcription and expected-answer branches

Two heads existed after PR #118 merged into master: c2f81d6a3b47 from the
transcription and schema work, and c9d3e4f5a6b7 from the expected-answer work
that landed in parallel. `alembic upgrade head` refuses to run with more than
one head, so this unifies them.

No schema changes - both branches touch different tables.

Revision ID: d4a9f2e81c65
Revises: c2f81d6a3b47, c9d3e4f5a6b7
Create Date: 2026-10-12 03:00:00.000000
"""

from alembic import op  # noqa: F401  # required by alembic's module contract

revision = "d4a9f2e81c65"
down_revision = ("c2f81d6a3b47", "c9d3e4f5a6b7")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
