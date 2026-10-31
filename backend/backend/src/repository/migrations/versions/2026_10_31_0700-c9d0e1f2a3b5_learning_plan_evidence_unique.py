"""Prevent a completion receipt from advancing more than one learning-plan step."""

from alembic import op


revision = "c9d0e1f2a3b5"
down_revision = "b8c9d0e1f2a4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "uq_learning_plan_drill_evidence",
        "learning_plan_step_progress",
        ["evidence_kind", "evidence_id"],
        unique=True,
        postgresql_where=(
            "evidence_kind IN ('drill_completion', 'pronunciation_session', "
            "'structure_practice', 'pacing_session')"
        ),
    )


def downgrade():
    op.drop_index(
        "uq_learning_plan_drill_evidence",
        "learning_plan_step_progress",
    )
