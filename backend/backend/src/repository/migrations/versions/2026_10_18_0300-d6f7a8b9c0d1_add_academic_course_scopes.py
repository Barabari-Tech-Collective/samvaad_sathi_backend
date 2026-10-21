"""Explicit academic course scopes; legacy nulls do not grant course access."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d6f7a8b9c0d1"
down_revision = "c5e6f7a8b9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "team_member", sa.Column("course_ids", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "user", sa.Column("enrolled_course_ids", postgresql.JSONB(), nullable=True)
    )
    op.add_column("interview", sa.Column("course_id", sa.String(128), nullable=True))
    op.create_index("ix_interview_course_id", "interview", ["course_id"])
    op.create_index(
        "ix_user_enrolled_course_ids",
        "user",
        ["enrolled_course_ids"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_user_enrolled_course_ids", table_name="user")
    op.drop_index("ix_interview_course_id", table_name="interview")
    op.drop_column("interview", "course_id")
    op.drop_column("user", "enrolled_course_ids")
    op.drop_column("team_member", "course_ids")
