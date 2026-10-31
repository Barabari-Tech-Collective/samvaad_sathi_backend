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


# Staff are in India: "today" is an Indian day.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _task(id, title, count, detail, owner, action, href, attention, oldest_at=None):
    return {
        "id": id,
        "title": title,
        "count": count,
        "detail": detail,
        "owner": owner,
        "action": {"label": action, "href": href},
        "attention": attention,
        # When the oldest pending item became due, so the Overview can say how long it waited.
        "oldest_at": oldest_at.isoformat() if oldest_at else None,
    }


def _interview_scope(user):
    """Admins see every interview; anyone else only those they host."""
    scope = [interviews.student_only()]
    if not is_admin_user(user):
        scope.append(HumanInterview.host_id == user.id)
    return scope


def _day_bounds(now):
    local = now.astimezone(IST)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + dt.timedelta(days=1)


async def _human_tasks(user, session, now):
    """Interviews this person runs: admins see all, others only the ones they host."""
    if not interviews.permitted(user, "read"):
        return []
    base = (
        sa.select(sa.func.count(HumanInterview.id), sa.func.min(HumanInterview.ends_at))
        .join(User, User.id == HumanInterview.student_id)
        .where(*_interview_scope(user))
    )

    async def stats(*where):
        n, oldest = (await session.execute(base.where(*where))).one()
        return int(n or 0), oldest

    day_start, day_end = _day_bounds(now)
    scheduled = HumanInterview.status == "scheduled"
    upcoming, _ = await stats(
        scheduled,
        HumanInterview.starts_at >= now,
        HumanInterview.starts_at < now + dt.timedelta(days=7),
    )
    today, _ = await stats(
        scheduled, HumanInterview.starts_at >= now, HumanInterview.starts_at < day_end
    )
    # Meeting time passing is not proof of attendance (pack sec 12).
    overdue, overdue_since = await stats(scheduled, HumanInterview.ends_at < now)
    no_feedback, feedback_since = await stats(
        HumanInterview.status == "completed", HumanInterview.result.is_(None)
    )
    unreleased, unreleased_since = await stats(
        HumanInterview.result.is_not(None), HumanInterview.result_released_at.is_(None)
    )
    owner = "You" if not is_admin_user(user) else "The interviewer"
    tasks = []
    if overdue:
        tasks.append(_task(
            "interviews_awaiting_attendance", "Awaiting attendance update", overdue,
            "The meeting time has passed but nobody recorded whether it happened.",
            owner, "Record what happened", HUMAN, True, overdue_since,
        ))
    if no_feedback:
        tasks.append(_task(
            "interviews_awaiting_feedback", "Feedback awaiting submission", no_feedback,
            "The interview took place; the result and feedback are not in yet.",
            owner, "Add the result", HUMAN, True, feedback_since,
        ))
    if unreleased:
        tasks.append(_task(
            "interviews_release_pending", "Result not shared yet", unreleased,
            "A result is recorded but the student cannot see it until it is shared.",
            "Placement lead", "Review and share", HUMAN, True, unreleased_since,
        ))
    if upcoming:
        tasks.append(_task(
            "interviews_upcoming", "Human interviews in the next 7 days", upcoming,
            f"{today} today." if today else "None today.",
            owner, "See the schedule", HUMAN, False,
        ))
    return tasks


async def _today(user, session, now):
    """Today's scheduled human interviews, in order, for the people who may see them.
    Same scope as the human-interview list: the student's name is already visible there."""
    if not interviews.permitted(user, "read"):
        return []
    day_start, day_end = _day_bounds(now)
    rows = (
        await session.execute(
            sa.select(HumanInterview, User.name)
            .join(User, User.id == HumanInterview.student_id)
            .where(
                *_interview_scope(user),
                HumanInterview.status == "scheduled",
                HumanInterview.starts_at >= day_start,
                HumanInterview.starts_at < day_end,
            )
            .order_by(HumanInterview.starts_at)
            .limit(8)
        )
    ).all()
    return [
        {
            "id": row.id,
            "starts_at": row.starts_at.isoformat(),
            "ends_at": row.ends_at.isoformat(),
            "student_name": name,
            "round": row.role_name or row.title,
            "can_join": interviews.joinable(row, now),
            # Joining happens on the interviews list; otherwise open the one record.
            "href": (
                HUMAN
                if interviews.joinable(row, now)
                else f"/dashboard/samvaad/assessments/human/{row.id}"
            ),
        }
        for row, name in rows
    ]


async def _review_tasks(user, session):
    """Templates waiting for a reviewer. Only admins may review and publish them."""
    if not is_admin_user(user):
        return []
    rows = (
        await session.execute(
            sa.select(
                JobProfile.interview_mode,
                sa.func.count(JobProfile.id),
                sa.func.min(sa.func.coalesce(JobProfile.submitted_at, JobProfile.updated_at)),
            )
            .where(JobProfile.status == "under_review")
            .group_by(JobProfile.interview_mode)
        )
    ).all()
    counts = {mode: int(n) for mode, n, _ in rows}
    since = {mode: oldest for mode, _, oldest in rows}
    tasks = []
    if counts.get("assessment"):
        tasks.append(_task(
            "templates_review_assessment", "Assessment templates to review",
            counts["assessment"],
            "Students cannot take these until someone approves and publishes them.",
            "Reviewer", "Review and publish", "/dashboard/samvaad/roles", True,
            since["assessment"],
        ))
    if counts.get("practice"):
        tasks.append(_task(
            "templates_review_mock", "Mock interview templates to review",
            counts["practice"],
            "Students cannot practise with these until they are published.",
            "Reviewer", "Review and publish", "/dashboard/samvaad/practice-templates",
            True, since["practice"],
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
    oldest = now - dt.timedelta(minutes=max(a["oldest_age_minutes"] for a in alerts))
    return [_task(
        "processing_failures", "Answers waiting on processing", stuck,
        f"{failed} failed, {stuck - failed} stalled for over 10 minutes. "
        "Answers are saved; feedback needs recovery.",
        "Operations", "Open failures", "/dashboard/samvaad/alerts", True, oldest,
    )]


async def _selection_tasks(user, session):
    from src.models.db.opportunity import Opportunity
    from src.services import course_shortlists, opportunity_selection, opportunities

    if not course_shortlists.permission(user, "read"):
        return []
    courses = await course_shortlists.placement_course_ids(user, session)
    stmt = sa.select(Opportunity).where(Opportunity.status.in_(("open", "closed")))
    if courses is not None:
        stmt = stmt.where(Opportunity.course_id.in_(courses))
    tasks = []
    for opening in (
        await session.execute(stmt.order_by(Opportunity.id.desc()))
    ).scalars():
        predicate = await opportunities.authorize(
            user, opening.course_id, session, "read"
        )
        selection = await opportunity_selection.latest(session, opening.id)
        view = await opportunity_selection.detail(
            session, user, opening, selection, predicate
        )
        if not view["items"] or view["status"] == "released":
            continue
        if view["can_release"]:
            title, count, action = (
                "Opportunity outcomes awaiting release",
                len(view["items"]),
                "Review and release",
            )
        elif view["can_decide"]:
            count = sum(not i["decision"] for i in view["items"])
            title, action = (
                ("Opportunity decisions awaiting review", "Review decisions")
                if count
                else (
                    ("Opportunity review needs attention", "Resolve review blockers")
                    if view["lock_blockers"]
                    else ("Opportunity ready for lock review", "Review and lock")
                )
            )
            count = count or 1
        elif view["can_recommend"]:
            count = sum(not i["recommendation"] for i in view["items"])
            title, action = "Opportunity recommendations pending", "Review candidates"
        else:
            continue
        if count:
            tasks.append(
                _task(
                    f"selection_{opening.id}",
                    title,
                    count,
                    opening.title,
                    "You",
                    action,
                    f"/dashboard/samvaad/opportunities/{opening.id}#selection",
                    True,
                )
            )
    return tasks


@router.get("/tasks")
async def my_tasks(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    now = dt.datetime.now(dt.timezone.utc)
    tasks = [
        *await _human_tasks(user, session, now),
        *await _selection_tasks(user, session),
        *await _review_tasks(user, session),
        *await _operations_tasks(user, session, now),
    ]
    # Things to act on first, then things to know about.
    tasks.sort(key=lambda t: not t["attention"])
    response.headers["Cache-Control"] = "no-store"
    return {
        "tasks": tasks,
        "today": await _today(user, session, now),
        "generated_at": now.isoformat(),
    }
