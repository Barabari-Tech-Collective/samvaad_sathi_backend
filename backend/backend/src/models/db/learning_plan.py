"""Learning plans (architecture pack sec 4): ordered activities toward a learning goal.

A plan is authored as a draft and frozen when published; changing a published plan makes a
new version, so a learner always keeps the version they started. Steps are speaking drills,
mock interviews or short written reflections. A learner works through an assignment of one
plan version, given by staff in a course or self-started when the plan allows it."""

import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.repository.table import Base

DRILLS = ("warm_up", "think_fast", "pronunciation", "structure", "pace")


class LearningPlan(Base):
    __tablename__ = "learning_plan"
    __table_args__ = (
        sa.UniqueConstraint("created_by", "request_key", name="uq_learning_plan_request"),
        sa.CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_learning_plan_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(sa.String(160))
    goal: Mapped[str] = mapped_column(sa.Text)
    description: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(16), server_default="draft")
    # Content version: 1 for the first plan, +1 for each revision of a published plan.
    version: Mapped[int] = mapped_column(server_default="1")
    # The published plan this draft revises.
    parent_id: Mapped[int | None] = mapped_column(sa.ForeignKey("learning_plan.id", ondelete="RESTRICT"))
    # Learners may start it themselves, not only when assigned.
    self_start: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.false())
    # Concurrency for edits.
    row_version: Mapped[int] = mapped_column(server_default="1")
    request_key: Mapped[str] = mapped_column(sa.String(36))
    created_by: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[datetime.datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )
    published_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))


class LearningPlanStep(Base):
    __tablename__ = "learning_plan_step"
    __table_args__ = (
        sa.UniqueConstraint("plan_id", "position", name="uq_learning_plan_step_position"),
        sa.CheckConstraint("kind IN ('drill', 'mock', 'reflection')", name="ck_learning_plan_step_kind"),
        sa.CheckConstraint(
            "(kind = 'drill') = (module IS NOT NULL) AND (kind = 'mock') = (job_profile_id IS NOT NULL)",
            name="ck_learning_plan_step_target",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(sa.ForeignKey("learning_plan.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(sa.Integer)
    kind: Mapped[str] = mapped_column(sa.String(16))
    module: Mapped[str | None] = mapped_column(sa.String(32))
    job_profile_id: Mapped[int | None] = mapped_column(sa.ForeignKey("job_profile.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(sa.String(160))
    instructions: Mapped[str | None] = mapped_column(sa.Text)


class LearningPlanAssignment(Base):
    __tablename__ = "learning_plan_assignment"
    __table_args__ = (
        sa.UniqueConstraint("student_id", "request_key", name="uq_learning_plan_assignment_request"),
        sa.CheckConstraint("source IN ('assigned', 'self')", name="ck_learning_plan_assignment_source"),
        sa.CheckConstraint(
            "status IN ('active', 'completed', 'cancelled')", name="ck_learning_plan_assignment_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(sa.ForeignKey("learning_plan.id", ondelete="RESTRICT"), index=True)
    student_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[str] = mapped_column(sa.String(128), index=True)
    source: Mapped[str] = mapped_column(sa.String(16))
    assigned_by: Mapped[int | None] = mapped_column(sa.Integer)
    reason: Mapped[str | None] = mapped_column(sa.Text)
    due_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    status: Mapped[str] = mapped_column(sa.String(16), server_default="active")
    version: Mapped[int] = mapped_column(server_default="1")
    request_key: Mapped[str] = mapped_column(sa.String(36))
    request_hash: Mapped[str] = mapped_column(sa.String(64))
    created_at: Mapped[datetime.datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    closed_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))


class LearningPlanStepProgress(Base):
    __tablename__ = "learning_plan_step_progress"
    __table_args__ = (
        sa.UniqueConstraint("assignment_id", "step_id", name="uq_learning_plan_step_progress"),
        sa.Index(
            "uq_learning_plan_drill_evidence",
            "evidence_kind",
            "evidence_id",
            unique=True,
            postgresql_where=sa.text(
                "evidence_kind IN ('drill_completion', 'pronunciation_session', "
                "'structure_practice', 'pacing_session')"
            ),
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('drill_completion', 'pronunciation_session', "
            "'structure_practice', 'pacing_session', 'interview', 'reflection')",
            name="ck_learning_plan_progress_evidence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        sa.ForeignKey("learning_plan_assignment.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[int] = mapped_column(sa.ForeignKey("learning_plan_step.id", ondelete="RESTRICT"))
    evidence_kind: Mapped[str] = mapped_column(sa.String(24))
    evidence_id: Mapped[int | None] = mapped_column(sa.Integer)
    reflection: Mapped[str | None] = mapped_column(sa.Text)
    completed_at: Mapped[datetime.datetime] = mapped_column(sa.DateTime(timezone=True))
