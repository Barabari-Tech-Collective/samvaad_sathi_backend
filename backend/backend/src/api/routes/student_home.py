"""One bounded, private read model for the student's home-page next action."""

import datetime as dt
from urllib.parse import quote

import fastapi
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.human_interview import HumanInterview
from src.models.db.interview import Interview
from src.models.db.job_profile import JobProfile
from src.models.db.learning_plan import LearningPlanAssignment
from src.models.db.mock_assignment import MockInterviewAssignment
from src.models.db.user import User
from src.services import human_interviews, learning_plans
from src.services.practice_hub import build_practice_hub
from src.services.sampark_enrollment_sync import freshness_predicate

router = fastapi.APIRouter(prefix="/v2/student-home", tags=["student-home"])
UTC = dt.timezone.utc


def _enrolled(course_column):
    return User.enrolled_course_ids.op("?")(course_column), freshness_predicate()


@router.get("/today")
async def today(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
):
    """Return exactly one safe next action, plus the private seven-day activity series."""
    response.headers["Cache-Control"] = "no-store"
    now = dt.datetime.now(UTC)
    activity = (await build_practice_hub(session, user_id=user.id, now=now)).activity

    human_rows = (
        await session.execute(
            sa.select(HumanInterview)
            .join(User, User.id == HumanInterview.student_id)
            .where(
                HumanInterview.student_id == user.id,
                HumanInterview.status == "scheduled",
                HumanInterview.ends_at > now,
                *_enrolled(HumanInterview.course_id),
            )
            .order_by(
                sa.case((HumanInterview.starts_at - dt.timedelta(minutes=15) <= now, 0), else_=1),
                HumanInterview.starts_at,
            )
            .limit(1)
        )
    ).scalars().all()
    if human_rows:
        row = human_rows[0]
        return {
            "action": {
                "kind": "human_interview",
                "title": "Join your Human Interview" if human_interviews.joinable(row, now) else row.title,
                "detail": "Your verified meeting access is open." if human_interviews.joinable(row, now) else f"Scheduled {row.starts_at.isoformat()}.",
                "href": "/human-interviews",
                "joinable": human_interviews.joinable(row, now),
            },
            "activity": activity,
        }

    assessment = (
        await session.execute(
            sa.select(Interview)
            .outerjoin(User, User.id == Interview.user_id)
            .where(
                Interview.user_id == user.id,
                Interview.interview_mode == "assessment",
                Interview.status.in_(("active", "paused", "preparing")),
                sa.or_(Interview.course_id.is_(None), sa.and_(*_enrolled(Interview.course_id))),
            )
            .order_by(Interview.created_at.desc(), Interview.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if assessment:
        return {"action": {"kind": "ai_interview", "title": f"Continue AI Interview: {assessment.track}", "detail": "Your placement assessment is ready to continue.", "href": f"/interview?interviewId={assessment.id}&interviewMode=assessment"}, "activity": activity}

    plans = list(
        (
            await session.execute(
                sa.select(LearningPlanAssignment)
                .join(User, User.id == LearningPlanAssignment.student_id)
                .where(
                    LearningPlanAssignment.student_id == user.id,
                    LearningPlanAssignment.status == "active",
                    *_enrolled(LearningPlanAssignment.course_id),
                )
                .order_by(LearningPlanAssignment.created_at.desc(), LearningPlanAssignment.id.desc())
                .limit(1)
            )
        ).scalars().all()
    )
    if plans:
        plan = (await learning_plans.assignment_views(session, plans))[0]
        current = next((step for step in plan["steps"] if step["status"] == "current"), None)
        if current:
            drill_paths = {
                "warm_up": "/warm-up-chat",
                "think_fast": "/think-fast",
                "pronunciation": "/pronunciation-practice",
                "structure": "/structure-your-answer",
                "pace": "/control-your-pace",
            }
            if current["kind"] == "drill" and current["module"] in drill_paths:
                href = f"{drill_paths[current['module']]}?planAssignment={plan['id']}&planStep={current['id']}"
            elif current["kind"] == "mock" and current["job_profile_id"]:
                href = (
                    "/interview-start?mode=practice"
                    f"&jobProfileId={current['job_profile_id']}&courseId={quote(plan['course_id'], safe='')}"
                )
            else:
                href = "/coaching"
            return {"action": {"kind": "learning_plan", "title": plan["plan"]["title"], "detail": f"Learning Plan · Step {current['position']}: {current['title']}", "href": href}, "activity": activity}

    mock = (
        await session.execute(
            sa.select(MockInterviewAssignment, JobProfile.job_name)
            .join(JobProfile, JobProfile.id == MockInterviewAssignment.job_profile_id)
            .join(User, User.id == MockInterviewAssignment.student_id)
            .where(
                MockInterviewAssignment.student_id == user.id,
                MockInterviewAssignment.status == "assigned",
                *_enrolled(MockInterviewAssignment.course_id),
            )
            .order_by(MockInterviewAssignment.due_date.nulls_last(), MockInterviewAssignment.created_at)
            .limit(1)
        )
    ).first()
    if mock:
        row, name = mock
        return {"action": {"kind": "mock_assignment", "title": f"Assigned Mock Interview: {name}", "detail": "Assigned by your program team.", "href": f"/interview-start?mode=practice&jobProfileId={row.job_profile_id}&courseId={quote(row.course_id, safe='')}"}, "activity": activity}
    return {"action": None, "activity": activity}
