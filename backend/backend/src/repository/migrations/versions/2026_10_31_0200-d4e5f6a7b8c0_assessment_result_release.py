"""Per-template release of AI assessment results (architecture pack F16)."""

from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c0"
down_revision = "c3d4e5f6a7b9"
branch_labels = None
depends_on = None

RULE = "result_release IN ('immediate', 'review')"


def upgrade():
    # Defaults keep today's behaviour: results reach the student as soon as scoring finishes.
    op.add_column(
        "job_profile",
        sa.Column("result_release", sa.String(16), nullable=False, server_default="immediate"),
    )
    op.create_check_constraint("ck_job_profile_result_release", "job_profile", RULE)
    # The rule is frozen onto each sitting when it starts, so a later template change never
    # hides or reveals a result someone already has.
    op.add_column(
        "interview",
        sa.Column("result_release", sa.String(16), nullable=False, server_default="immediate"),
    )
    op.create_check_constraint("ck_interview_result_release", "interview", RULE)
    op.add_column("interview", sa.Column("result_released_at", sa.DateTime(timezone=True)))
    op.add_column("interview", sa.Column("result_released_by", sa.Integer()))


def downgrade():
    op.drop_column("interview", "result_released_by")
    op.drop_column("interview", "result_released_at")
    op.drop_constraint("ck_interview_result_release", "interview", type_="check")
    op.drop_column("interview", "result_release")
    op.drop_constraint("ck_job_profile_result_release", "job_profile", type_="check")
    op.drop_column("job_profile", "result_release")
