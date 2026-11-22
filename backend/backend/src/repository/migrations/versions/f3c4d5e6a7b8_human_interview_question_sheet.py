"""Human interviews get a question sheet; scorecards score each answer on a 1-7 scale.
Staff can edit their own name and contact number.

Revision ID: f3c4d5e6a7b8
Revises: e2b3c4d5f6a7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f3c4d5e6a7b8"
down_revision: str | None = "e2b3c4d5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "human_interview",
        sa.Column("question_sheet", JSONB(), server_default="[]", nullable=False),
    )
    op.add_column(
        "human_scorecard", sa.Column("answers", JSONB(), server_default="[]", nullable=False)
    )
    op.add_column("user", sa.Column("contact_number", sa.String(20), nullable=True))
    op.add_column(
        "user",
        sa.Column("name_edited", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    # Scorecards written before this change used 0-5; new ones use 1-7.
    op.add_column(
        "human_scorecard", sa.Column("scale", sa.Integer(), server_default="5", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("user", "name_edited")
    op.drop_column("user", "contact_number")
    op.drop_column("human_scorecard", "scale")
    op.drop_column("human_scorecard", "answers")
    op.drop_column("human_interview", "question_sheet")
