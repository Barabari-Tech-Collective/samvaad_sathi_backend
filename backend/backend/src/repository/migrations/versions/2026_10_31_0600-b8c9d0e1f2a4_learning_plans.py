"""Learning plans, their steps, learner assignments and step progress (pack sec 4)."""

from alembic import op
import sqlalchemy as sa

revision = "b8c9d0e1f2a4"
down_revision = "a7b8c9d0e1f3"
branch_labels = None
depends_on = None

NOW = sa.func.now()


def upgrade():
    op.create_table(
        "learning_plan",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("learning_plan.id", ondelete="RESTRICT")),
        sa.Column("self_start", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("created_by", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("created_by", "request_key", name="uq_learning_plan_request"),
        sa.CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_learning_plan_status"),
    )
    op.create_table(
        "learning_plan_step",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("learning_plan.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("module", sa.String(32)),
        sa.Column("job_profile_id", sa.Integer(), sa.ForeignKey("job_profile.id", ondelete="RESTRICT")),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("instructions", sa.Text()),
        sa.UniqueConstraint("plan_id", "position", name="uq_learning_plan_step_position"),
        sa.CheckConstraint("kind IN ('drill', 'mock', 'reflection')", name="ck_learning_plan_step_kind"),
        sa.CheckConstraint(
            "(kind = 'drill') = (module IS NOT NULL) AND (kind = 'mock') = (job_profile_id IS NOT NULL)",
            name="ck_learning_plan_step_target",
        ),
    )
    op.create_index("ix_learning_plan_step_plan_id", "learning_plan_step", ["plan_id"])
    op.create_table(
        "learning_plan_assignment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("learning_plan.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", sa.String(128), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("assigned_by", sa.Integer()),
        sa.Column("reason", sa.Text()),
        sa.Column("due_date", sa.Date()),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("student_id", "request_key", name="uq_learning_plan_assignment_request"),
        sa.CheckConstraint("source IN ('assigned', 'self')", name="ck_learning_plan_assignment_source"),
        sa.CheckConstraint(
            "status IN ('active', 'completed', 'cancelled')", name="ck_learning_plan_assignment_status"
        ),
    )
    for column in ("plan_id", "student_id", "course_id"):
        op.create_index(f"ix_learning_plan_assignment_{column}", "learning_plan_assignment", [column])
    op.create_table(
        "learning_plan_step_progress",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "assignment_id",
            sa.Integer(),
            sa.ForeignKey("learning_plan_assignment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_id", sa.Integer(), sa.ForeignKey("learning_plan_step.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("evidence_kind", sa.String(24), nullable=False),
        sa.Column("evidence_id", sa.Integer()),
        sa.Column("reflection", sa.Text()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("assignment_id", "step_id", name="uq_learning_plan_step_progress"),
        sa.CheckConstraint(
            "evidence_kind IN ('drill_completion', 'interview', 'reflection')",
            name="ck_learning_plan_progress_evidence",
        ),
    )
    op.create_index("ix_learning_plan_step_progress_assignment_id", "learning_plan_step_progress", ["assignment_id"])


def downgrade():
    op.drop_index("ix_learning_plan_step_progress_assignment_id", "learning_plan_step_progress")
    op.drop_table("learning_plan_step_progress")
    for column in ("course_id", "student_id", "plan_id"):
        op.drop_index(f"ix_learning_plan_assignment_{column}", "learning_plan_assignment")
    op.drop_table("learning_plan_assignment")
    op.drop_index("ix_learning_plan_step_plan_id", "learning_plan_step")
    op.drop_table("learning_plan_step")
    op.drop_table("learning_plan")
