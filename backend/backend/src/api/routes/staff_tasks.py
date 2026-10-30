"""The staff Overview's work queue: what needs this person now, and what to do next.

Architecture pack sec 13 (task-oriented Overview), sec 10 (clear next owner) and sec 12
(truthful status). Every task is filtered by the same rules as the page it links to, carries
its next action, and is omitted when nothing is pending, so the Overview never shows a wall
of zeros. Counts only; no student answers, feedback or names leave this endpoint.
"""

import datetime as dt

import fastapi
import sqlalchemy as sa

from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.session import get_async_session
from src.models.db.human_interview import HumanInterview
from src.models.db.job_profile import JobProfile
from src.models.db.user import User
from src.services import human_interviews as interviews
from src.services.analytics import AnalyticsService

router = fastapi.APIRouter(prefix="/v2/staff", tags=["staff-tasks"])

HUMAN = "/dashboard/samvaad/human-interviews"


def _task(id, title, count, detail, owner, action, href, attention):
    return {
        "id": id,
        "title": title,
        "count": count,
        "detail": detail,
        "owner": owner,
        "action": {"label": action, "href": href},
        "attention": attention,
    }


async def _human_tasks(user, session, now):
    """Interviews this person runs: admins see all, others only the ones they host."""
    if not interviews.permitted(user, "read"):
        return []
    scope = [interviews.student_only()]
    if not is_admin_user(user):
        scope.append(HumanInterview.host_id == user.id)
    base = (
        sa.select(sa.func.count(HumanInterview.id))
        .join(User, User.id == HumanInterview.student_id)
        .where(*scope)
    )

    async def count(*where):
        return int((await session.execute(base.where(*where))).scalar_one() or 0)

    end_of_today = now.replace(hour=23, minute=59, second=59)
    scheduled = HumanInterview.status == "scheduled"
    upcoming = await count(
        scheduled,
        HumanInterview.starts_at >= now,
        HumanInterview.starts_at < now + dt.timedelta(days=7),
    )
    today = await count(
        scheduled, HumanInterview.starts_at >= now, HumanInterview.starts_at <= end_of_today
    )
    # Meeting time passing is not proof of attendance (pack sec 12).
    overdue = await count(scheduled, HumanInterview.ends_at < now)
    no_feedback = await count(
        HumanInterview.status == "completed", HumanInterview.result.is_(None)
    )
    unreleased = await count(
        HumanInterview.result.is_not(None), HumanInterview.result_released_at.is_(None)
    )
    owner = "You" if not is_admin_user(user) else "The interviewer"
    tasks = []
    if overdue:
        tasks.append(_task(
            "interviews_awaiting_attendance", "Awaiting attendance update", overdue,
            "The meeting time has passed but nobody recorded whether it happened.",
            owner, "Record what happened", HUMAN, True,
        ))
    if no_feedback:
        tasks.append(_task(
            "interviews_awaiting_feedback", "Feedback awaiting submission", no_feedback,
            "The interview took place; the result and feedback are not in yet.",
            owner, "Add the result", HUMAN, True,
        ))
    if unreleased:
        tasks.append(_task(
            "interviews_release_pending", "Result not shared yet", unreleased,
            "A result is recorded but the student cannot see it until it is shared.",
            "Placement lead", "Review and share", HUMAN, True,
        ))
    if upcoming:
        tasks.append(_task(
            "interviews_upcoming", "Human interviews in the next 7 days", upcoming,
            f"{today} today." if today else "None today.",
            owner, "See the schedule", HUMAN, False,
        ))
    return tasks


async def _review_tasks(user, session):
    """Templates waiting for a reviewer. Only admins may review and publish them."""
    if not is_admin_user(user):
        return []
    rows = (
        await session.execute(
            sa.select(JobProfile.interview_mode, sa.func.count(JobProfile.id))
            .where(JobProfile.status == "under_review")
            .group_by(JobProfile.interview_mode)
        )
    ).all()
    counts = {mode: int(n) for mode, n in rows}
    tasks = []
    if counts.get("assessment"):
        tasks.append(_task(
            "templates_review_assessment", "Assessment templates to review",
            counts["assessment"],
            "Students cannot take these until someone approves and publishes them.",
            "Reviewer", "Review and publish", "/dashboard/samvaad/roles", True,
        ))
    if counts.get("practice"):
        tasks.append(_task(
            "templates_review_mock", "Mock interview templates to review",
            counts["practice"],
            "Students cannot practise with these until they are published.",
            "Reviewer", "Review and publish", "/dashboard/samvaad/practice-templates",
            True,
        ))
    return tasks


async def _operations_tasks(user, session, now):
    """Saved answers whose processing failed or stalled. Operations is admins only."""
    if not is_admin_user(user):
        return []
    alerts = await AnalyticsService(session)._answer_processing_alerts(
        user_id=None, start_date=None, end_date=None, now=now
    )
    stuck = sum(a["count"] for a in alerts)
    if not stuck:
        return []
    failed = sum(a["count"] for a in alerts if a["severity"] == "high")
    return [_task(
        "processing_failures", "Answers waiting on processing", stuck,
        f"{failed} failed, {stuck - failed} stalled for over 10 minutes. "
        "Answers are saved; feedback needs recovery.",
        "Operations", "Open failures", "/dashboard/samvaad/alerts", True,
    )]


@router.get("/tasks")
async def my_tasks(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    now = dt.datetime.now(dt.timezone.utc)
    tasks = [
        *await _human_tasks(user, session, now),
        *await _review_tasks(user, session),
        *await _operations_tasks(user, session, now),
    ]
    # Things to act on first, then things to know about.
    tasks.sort(key=lambda t: not t["attention"])
    response.headers["Cache-Control"] = "no-store"
    return {"tasks": tasks, "generated_at": now.isoformat()}
