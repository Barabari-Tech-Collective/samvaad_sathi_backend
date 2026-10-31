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
        ["evidence_id"],
        unique=True,
        postgresql_where="evidence_kind = 'drill_completion'",
    )


def downgrade():
    op.drop_index(
        "uq_learning_plan_drill_evidence",
        "learning_plan_step_progress",
    )
