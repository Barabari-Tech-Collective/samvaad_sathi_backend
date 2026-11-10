"""Learning plan progress (architecture pack sec 4).

Steps are done in order. A drill step completes when the learner records that drill after the
previous step was done (or after the plan started); a mock step when they finish a mock of
that template in the plan's course; a reflection step when they submit one. Progress is
settled when a plan is read and kept, so it never depends on the client reporting it.
"""

import datetime as dt

import sqlalchemy as sa

from src.models.db.interview import Interview
from src.models.db.job_profile import JobProfile
from src.models.db.learning_plan import (
    LearningPlan,
    LearningPlanAssignment,
    LearningPlanStep,
    LearningPlanStepProgress,
)
from src.models.db.practice_drill_completion import PracticeDrillCompletion

UTC = dt.timezone.utc

DRILL_LABELS = {
    "warm_up": "Warm-up chat",
    "think_fast": "Think fast",
    "pronunciation": "Pronunciation",
    "structure": "Plan your answers",
    "pace": "Speak at a good speed",
}


async def steps_for(session, plan_ids):
    rows = (
        await session.execute(
            sa.select(LearningPlanStep, JobProfile.job_name)
            .outerjoin(JobProfile, JobProfile.id == LearningPlanStep.job_profile_id)
            .where(LearningPlanStep.plan_id.in_(plan_ids))
            .order_by(LearningPlanStep.plan_id, LearningPlanStep.position)
        )
    ).all()
    out: dict[int, list] = {pid: [] for pid in plan_ids}
    for step, job_name in rows:
        out[step.plan_id].append((step, job_name))
    return out


def step_view(step, job_name, done=None, current=False):
    view = {
        "id": step.id,
        "position": step.position,
        "kind": step.kind,
        "title": step.title,
        "instructions": step.instructions,
        "module": step.module,
        "module_label": DRILL_LABELS.get(step.module) if step.module else None,
        "job_profile_id": step.job_profile_id,
        "job_name": job_name,
    }
    if done is not None or current:
        view["status"] = "done" if done else ("current" if current else "upcoming")
        view["completed_at"] = done.completed_at if done else None
        view["reflection"] = done.reflection if done else None
    return view


def plan_view(plan, steps):
    return {
        "id": plan.id,
        "title": plan.title,
        "goal": plan.goal,
        "description": plan.description,
        "status": plan.status,
        "version": plan.version,
        "parent_id": plan.parent_id,
        "self_start": plan.self_start,
        "row_version": plan.row_version,
        "published_at": plan.published_at,
        "steps": [step_view(s, name) for s, name in steps],
    }


async def _evidence(session, assignment, step, since):
    if step.kind == "drill":
        # A generic drill receipt is not enough: it has no course/plan identity. The learner
        # must explicitly bind an owned receipt to the current step through the guarded route.
        return None
    if step.kind == "mock":
        row = (
            await session.execute(
                sa.select(Interview.id, Interview.completed_at)
                .where(
                    Interview.user_id == assignment.student_id,
                    Interview.course_id == assignment.course_id,
                    Interview.job_profile_id == step.job_profile_id,
                    Interview.interview_mode == "practice",
                    Interview.status == "completed",
                    Interview.completed_at >= since,
                )
                .order_by(Interview.completed_at)
                .limit(1)
            )
        ).first()
        return ("interview", row.id, row.completed_at) if row else None
    return None  # A reflection waits for the learner to write it.


async def settle(session, assignments):
    """Record newly evidenced steps, in order, and close plans whose steps are all done."""
    changed = False
    active = [a for a in assignments if a.status == "active"]
    if not active:
        return
    steps = await steps_for(session, {a.plan_id for a in active})
    for a in active:
        done = await progress_for(session, [a.id])
        since = a.created_at
        for step, _ in steps[a.plan_id]:
            record = done.get((a.id, step.id))
            if record:
                since = record.completed_at
                continue
            found = await _evidence(session, a, step, since)
            if not found:
                break
            kind, evidence_id, at = found
            session.add(
                LearningPlanStepProgress(
                    assignment_id=a.id, step_id=step.id, evidence_kind=kind,
                    evidence_id=evidence_id, completed_at=at,
                )
            )
            since, changed = at, True
        else:
            if steps[a.plan_id]:
                a.status, a.closed_at, a.version = "completed", since, a.version + 1
                changed = True
    if changed:
        await session.commit()


async def progress_for(session, assignment_ids):
    if not assignment_ids:
        return {}
    rows = (
        await session.execute(
            sa.select(LearningPlanStepProgress).where(
                LearningPlanStepProgress.assignment_id.in_(assignment_ids)
            )
        )
    ).scalars()
    return {(p.assignment_id, p.step_id): p for p in rows}


async def assignment_views(session, assignments):
    """Each assignment with its plan version, steps, and where the learner is."""
    await settle(session, assignments)
    if not assignments:
        return []
    plans = {
        p.id: p
        for p in (
            await session.execute(
                sa.select(LearningPlan).where(LearningPlan.id.in_({a.plan_id for a in assignments}))
            )
        ).scalars()
    }
    steps = await steps_for(session, set(plans))
    done = await progress_for(session, [a.id for a in assignments])
    out = []
    for a in assignments:
        plan = plans[a.plan_id]
        views, current_found = [], False
        for step, name in steps[a.plan_id]:
            record = done.get((a.id, step.id))
            current = not record and not current_found and a.status == "active"
            current_found = current_found or current
            views.append(step_view(step, name, done=record or False, current=current))
        out.append(
            {
                "id": a.id,
                "course_id": a.course_id,
                "source": a.source,
                "status": a.status,
                "version": a.version,
                "reason": a.reason,
                "due_date": a.due_date,
                "created_at": a.created_at,
                "closed_at": a.closed_at,
                "plan": {
                    "id": plan.id,
                    "title": plan.title,
                    "goal": plan.goal,
                    "version": plan.version,
                },
                "steps": views,
                "completed_steps": sum(1 for v in views if v["status"] == "done"),
                "total_steps": len(views),
            }
        )
    return out


async def assignments_where(session, predicate, limit=50):
    return (
        await session.execute(
            sa.select(LearningPlanAssignment)
            .where(predicate)
            .order_by(LearningPlanAssignment.created_at.desc(), LearningPlanAssignment.id.desc())
            .limit(limit)
        )
    ).scalars().all()
