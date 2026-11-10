"""One Coaching page across every course a person may coach. Writes reuse the existing rules."""

import datetime as dt

import sqlalchemy as sa

from src.models.db.coaching_assignment import CoachingAssignment
from src.models.db.interview import Interview
from src.models.db.question_attempt import QuestionAttempt
from src.models.db.job_profile import JobProfile
from src.models.db.learning_plan import LearningPlan, LearningPlanAssignment, LearningPlanStep
from src.models.db.mock_assignment import MockInterviewAssignment
from src.models.db.notification import Notification
from src.models.db.practice_drill_completion import PracticeDrillCompletion
from src.models.db.summary_report import SummaryReport
from src.models.db.user import User
from src.services import learner_hub as hub
from src.services import learning_plans as plans

UTC = dt.timezone.utc
DRILLS = {
    "warm_up": ("Warm-up chat", "A short, easy chat to get speaking", "A short warm-up will get you comfortable speaking again."),
    "think_fast": ("Think fast", "Quick answers to surprise questions", "This will help you answer quickly when a question surprises you."),
    "pronunciation": ("Pronunciation", "Say difficult words clearly", "This will help your words come through clearly."),
    "structure": ("Structure your answer", "Give every answer a clear shape", "This will help you give answers a clear start, middle and end."),
    "pace": ("Control your pace", "Speak at a steady speed, with pauses", "This will help you slow down and speak at a steady pace."),
}
REMIND_GAP_DAYS = 3
SIGNALS = {
    "session_blocked": ("Session blocked", "Check what stopped this session. On its own, this does not mean the learner did anything wrong."),
    "report_delayed": ("Report not yet available", "The report is late. Check it is still being made before asking the learner to redo anything."),
    "open_over_two_hours": ("Open for over two hours", "Ask the learner if they need help. A session left open is not always stuck."),
    "answer_processing_failed": ("Answer processing failed", "The learner may need help saving an answer. Saved answers are kept while you check."),
}


def suggest(person: hub.Learner) -> dict:
    if person.status == "never":
        return {"why": "Joined but never started", "module": "warm_up"}
    if person.status == "idle":
        days = (dt.datetime.now(UTC) - person.last_at).days if person.last_at else None
        return {"why": f"No activity for {days} days" if days else "No recent activity", "module": "warm_up"}
    if person.status == "low":
        speech_weaker = person.speech is not None and (person.knowledge is None or person.speech < person.knowledge)
        return (
            {"why": "Low score twice · Speech is the weaker side", "module": "pace"}
            if speech_weaker
            else {"why": "Low score twice · Knowledge is the weaker side", "module": "structure"}
        )
    return {"why": "Doing fine", "module": ""}


async def load_items(session, context, ids: set[int]) -> list[dict]:
    """Every drill, mock and plan for these learners, in one shape."""
    if not ids:
        return []
    names = dict(
        (await session.execute(sa.select(User.id, User.name).where(User.id.in_(
            sa.select(CoachingAssignment.assigned_by).where(CoachingAssignment.student_id.in_(ids)).union(
                sa.select(MockInterviewAssignment.assigned_by).where(MockInterviewAssignment.student_id.in_(ids)),
                sa.select(LearningPlanAssignment.assigned_by).where(LearningPlanAssignment.student_id.in_(ids)),
            )
        )))).all()
    )
    today = dt.datetime.now(UTC).date()
    out: list[dict] = []

    def base(key, kind, row, name, sub, status, by, self_start=False):
        return {
            "key": key, "type": kind, "id": row.id, "student_id": row.student_id, "course_id": row.course_id,
            "name": name, "sub": sub, "status": status, "version": row.version, "reason": row.reason,
            "note": getattr(row, "note", None), "due_date": row.due_date,
            "due_days": (row.due_date - today).days if row.due_date else None,
            "by": by, "self_started": self_start, "created_at": row.created_at, "closed_at": row.closed_at,
        }

    for row in (await session.execute(sa.select(CoachingAssignment).where(CoachingAssignment.student_id.in_(ids)))).scalars():
        out.append(base(f"c{row.id}", "drill", row, DRILLS[row.module][0], "", row.status, names.get(row.assigned_by)) | {"module": row.module})
    mocks = (
        await session.execute(
            sa.select(MockInterviewAssignment, JobProfile.job_name)
            .join(JobProfile, JobProfile.id == MockInterviewAssignment.job_profile_id)
            .where(MockInterviewAssignment.student_id.in_(ids))
        )
    ).all()
    for row, job in mocks:
        out.append(base(f"m{row.id}", "mock", row, f"Mock Interview: {job}", "", row.status, names.get(row.assigned_by)))
    assigned = list((await session.execute(sa.select(LearningPlanAssignment).where(LearningPlanAssignment.student_id.in_(ids)))).scalars())
    views = await plans.assignment_views(session, assigned)
    for row, view in zip(assigned, views):
        done = sum(1 for s in view["steps"] if s["status"] == "done")
        total = len(view["steps"])
        status = {"active": "assigned"}.get(row.status, row.status)
        title = view.get("plan", {}).get("title") if isinstance(view.get("plan"), dict) else view.get("title")
        out.append(
            base(f"p{row.id}", "plan", row, title or "Learning Plan", f"Step {min(done + 1, total)} of {total}", status,
                 names.get(row.assigned_by), row.source != "staff") | {"plan_id": row.plan_id, "done": done, "total": total, "steps": view["steps"]}
        )
    add_score_change(context, out)
    return out


def add_score_change(context, items: list[dict]) -> None:
    """Score after a finished drill minus the score before it was assigned, when both exist."""
    by_user: dict[int, list[tuple[dt.datetime, float]]] = {}
    for row in context.interviews:
        if row[0] in context.scores:
            by_user.setdefault(row[1], []).append((row[4], context.scores[row[0]]))
    for item in items:
        item["change"] = None
        if item["type"] != "drill" or item["status"] != "completed" or not item["closed_at"]:
            continue
        rows = sorted(by_user.get(item["student_id"], []))
        before = [s for at, s in rows if at <= item["created_at"]]
        after = [s for at, s in rows if at >= item["closed_at"]]
        if before and after:
            item["change"] = round(after[0] - before[-1])


async def reminded(session, items: list[dict]) -> set[str]:
    since = dt.datetime.now(UTC) - dt.timedelta(days=REMIND_GAP_DAYS)
    rows = await session.execute(
        sa.select(Notification.related_type, Notification.related_id).where(
            Notification.kind == "coaching_reminder", Notification.created_at >= since
        )
    )
    prefix = {"coaching_assignment": "c", "mock_assignment": "m", "learning_plan_assignment": "p"}
    return {f"{prefix.get(t, '?')}{i}" for t, i in rows.all()}


async def nudged(session, ids: set[int]) -> set[int]:
    if not ids:
        return set()
    since = dt.datetime.now(UTC) - dt.timedelta(days=REMIND_GAP_DAYS)
    return set(
        (
            await session.execute(
                sa.select(Notification.recipient_id).where(
                    Notification.recipient_id.in_(ids), Notification.kind == "learner_nudge", Notification.created_at >= since
                )
            )
        ).scalars()
    )


async def drill_counts(session, ids: set[int]) -> dict[int, int]:
    if not ids:
        return {}
    return dict(
        (
            await session.execute(
                sa.select(PracticeDrillCompletion.user_id, sa.func.count())
                .where(PracticeDrillCompletion.user_id.in_(ids))
                .group_by(PracticeDrillCompletion.user_id)
            )
        ).all()
    )


def facts(person: hub.Learner, drills: int) -> str:
    if person.status == "never":
        return ""
    bits = [f"{person.interviews} mock {'interview' if person.interviews == 1 else 'interviews'}", f"{drills} {'drill' if drills == 1 else 'drills'}"]
    if person.last_score is not None:
        bits.append(f"Last score {round(person.last_score)}")
    return " · ".join(bits)


def person_out(context, person: hub.Learner, busy: bool, drills: int, was_nudged: bool) -> dict:
    return {
        "id": person.id, "name": person.name, "college": person.college_label, "status": person.status,
        "courses": [{"id": c, "name": context.course_names.get(c, c)} for c in person.courses],
        "suggest": suggest(person), "facts": facts(person, drills), "busy": busy, "reminded": was_nudged,
    }


async def practice_board(session, ids: set[int], signal: str, page: int, limit: int) -> dict:
    now = dt.datetime.now(UTC)
    window = now - dt.timedelta(days=30)
    has_report = sa.exists(
        sa.select(SummaryReport.id).where(
            SummaryReport.interview_id == Interview.id,
            sa.func.jsonb_typeof(SummaryReport.report_json) == "object",
            SummaryReport.report_json != {},
        )
    )
    failed = (
        sa.select(sa.func.count(QuestionAttempt.id))
        .where(QuestionAttempt.interview_id == Interview.id, QuestionAttempt.transcription_status == "failed")
        .correlate(Interview)
        .scalar_subquery()
    )
    issue = sa.case(
        (sa.or_(Interview.status.in_(["failed", "terminated"]), Interview.proctoring_terminated.is_(True)), "session_blocked"),
        (sa.and_(Interview.status == "completed", ~has_report, sa.func.coalesce(Interview.completed_at, Interview.created_at) < now - dt.timedelta(minutes=15)), "report_delayed"),
        (sa.and_(Interview.status.in_(["active", "paused"]), Interview.created_at < now - dt.timedelta(hours=2)), "open_over_two_hours"),
        (failed > 0, "answer_processing_failed"),
        else_=None,
    )
    base = sa.select(
        Interview.id.label("interview_id"), Interview.user_id, Interview.status, Interview.created_at,
        Interview.proctoring_terminated, issue.label("issue"), failed.label("failed_answers"),
    ).where(Interview.user_id.in_(ids or [-1]), Interview.interview_mode == "practice", Interview.created_at >= window).subquery()
    row = (
        await session.execute(
            sa.select(
                sa.func.count().filter(base.c.status == "completed").label("completed"),
                sa.func.count().filter(base.c.status.in_(["active", "paused"])).label("open"),
                sa.func.count().filter(base.c.issue == "session_blocked").label("blocked"),
                sa.func.count().filter(base.c.issue == "report_delayed").label("late"),
                sa.func.coalesce(sa.func.sum(base.c.failed_answers), 0).label("failed_answers"),
            )
        )
    ).one()
    by_signal = dict(
        (await session.execute(sa.select(base.c.issue, sa.func.count()).where(base.c.issue.is_not(None)).group_by(base.c.issue))).all()
    )
    where = base.c.issue.is_not(None) if signal == "all" else base.c.issue == signal
    total = (await session.execute(sa.select(sa.func.count()).select_from(base).where(where))).scalar_one()
    rows = (
        await session.execute(
            sa.select(base, User.name, User.university)
            .join(User, User.id == base.c.user_id)
            .where(where)
            .order_by(base.c.created_at.asc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).all()
    latest_failed = {}
    wanted = [r.interview_id for r in rows if r.issue == "answer_processing_failed"]
    if wanted:
        for attempt_id, interview_id in (
            await session.execute(
                sa.select(QuestionAttempt.id, QuestionAttempt.interview_id).where(
                    QuestionAttempt.interview_id.in_(wanted), QuestionAttempt.transcription_status == "failed"
                )
            )
        ).all():
            latest_failed.setdefault(interview_id, attempt_id)
    return {
        "numbers": {"completed": row.completed, "can_continue": row.open, "blocked": row.blocked, "reports_late": row.late, "failed_answers": int(row.failed_answers)},
        "signals": [{"key": k, "label": v[0], "count": int(by_signal.get(k, 0))} for k, v in SIGNALS.items()],
        "items": [
            {
                "interview_id": r.interview_id, "student_id": r.user_id, "name": r.name, "college": r.university,
                "issue": r.issue, "label": SIGNALS[r.issue][0], "guidance": SIGNALS[r.issue][1], "created_at": r.created_at,
                "failed_answers": int(r.failed_answers or 0), "attempt_id": latest_failed.get(r.interview_id),
            }
            for r in rows
        ],
        "total": total, "page": page, "limit": limit,
    }


async def plan_rows(session, ids: set[int], status: str | None) -> list[dict]:
    query = sa.select(LearningPlan).order_by(LearningPlan.updated_at.desc())
    if status:
        query = query.where(LearningPlan.status == status)
    found = list((await session.execute(query)).scalars())
    latest = {}
    for plan in found:
        latest.setdefault(plan.parent_id or plan.id, plan)
    steps = dict(
        (await session.execute(sa.select(LearningPlanStep.plan_id, sa.func.count()).group_by(LearningPlanStep.plan_id))).all()
    )
    counts: dict[int, dict[str, int]] = {}
    if ids:
        for plan_id, state, n in (
            await session.execute(
                sa.select(LearningPlanAssignment.plan_id, LearningPlanAssignment.status, sa.func.count())
                .where(LearningPlanAssignment.student_id.in_(ids))
                .group_by(LearningPlanAssignment.plan_id, LearningPlanAssignment.status)
            )
        ).all():
            counts.setdefault(plan_id, {})[state] = n
    return [
        {
            "id": p.id, "title": p.title, "goal": p.goal, "status": p.status, "version": p.version, "self_start": p.self_start,
            "row_version": p.row_version, "steps": int(steps.get(p.id, 0)),
            "active": counts.get(p.id, {}).get("active", 0), "completed": counts.get(p.id, {}).get("completed", 0),
        }
        for p in found
    ]
