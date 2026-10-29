"""Human interview rounds: role, instructions, interviewer, reschedule and released result."""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f7"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("human_interview", sa.Column("role_name", sa.String(160)))
    op.add_column("human_interview", sa.Column("instructions", sa.Text()))
    op.add_column("human_interview", sa.Column("interviewer_name", sa.String(120)))
    op.add_column(
        "human_interview",
        sa.Column("reschedule_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("human_interview", sa.Column("result", sa.String(24)))
    op.add_column("human_interview", sa.Column("feedback", sa.Text()))
    op.add_column(
        "human_interview",
        sa.Column("result_released_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "ck_human_interview_result",
        "human_interview",
        "result IS NULL OR result IN ('selected', 'next_round', 'on_hold', 'not_selected')",
    )


def downgrade():
    op.drop_constraint("ck_human_interview_result", "human_interview", type_="check")
    for column in (
        "result_released_at",
        "feedback",
        "result",
        "reschedule_count",
        "interviewer_name",
        "instructions",
        "role_name",
    ):
        op.drop_column("human_interview", column)
