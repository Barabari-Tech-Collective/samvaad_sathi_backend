"""Allow server-verified specialist drill sessions to advance learning plans."""

from alembic import op


revision = "d0e1f2a3b4c6"
down_revision = "c9d0e1f2a3b5"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index("uq_learning_plan_drill_evidence", "learning_plan_step_progress")
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
    op.drop_constraint(
        "ck_learning_plan_progress_evidence",
        "learning_plan_step_progress",
        type_="check",
    )
    op.create_check_constraint(
        "ck_learning_plan_progress_evidence",
        "learning_plan_step_progress",
        "evidence_kind IN ('drill_completion', 'pronunciation_session', "
        "'structure_practice', 'pacing_session', 'interview', 'reflection')",
    )


def downgrade():
    op.drop_constraint(
        "ck_learning_plan_progress_evidence",
        "learning_plan_step_progress",
        type_="check",
    )
    op.create_check_constraint(
        "ck_learning_plan_progress_evidence",
        "learning_plan_step_progress",
        "evidence_kind IN ('drill_completion', 'interview', 'reflection')",
    )
    op.drop_index("uq_learning_plan_drill_evidence", "learning_plan_step_progress")
    op.create_index(
        "uq_learning_plan_drill_evidence",
        "learning_plan_step_progress",
        ["evidence_id"],
        unique=True,
        postgresql_where="evidence_kind = 'drill_completion'",
    )
