"""One bounded, private read model for the student's home-page next action."""

import datetime as dt
from urllib.parse import quote

import fastapi
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.assessment_assignment import AssessmentAssignment
from src.models.db.coaching_assignment import CoachingAssignment
from src.models.db.human_interview import HumanInterview
from src.models.db.interview import Interview
from src.models.db.job_profile import JobProfile
from src.models.db.learning_plan import LearningPlanAssignment
from src.models.db.mock_assignment import MockInterviewAssignment
from src.models.db.practice_drill_completion import PracticeDrillCompletion
from src.models.db.summary_report import SummaryReport
from src.models.db.user import User
from src.services import human_interviews, learning_plans
from src.services.practice_hub import build_practice_hub
from src.services.sampark_enrollment_sync import freshness_predicate

router = fastapi.APIRouter(prefix="/v2/student-home", tags=["student-home"])
UTC = dt.timezone.utc
DRILL_PATHS = {
    "warm_up": "/warm-up-chat", "think_fast": "/think-fast", "pronunciation": "/pronunciation-practice",
    "structure": "/structure-your-answer", "pace": "/control-your-pace",
}
DRILL_NAMES = learning_plans.DRILL_LABELS


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
    hub = await build_practice_hub(session, user_id=user.id, now=now)
    activity = hub.activity
    has_assessments = bool(
        (
            await session.execute(
                sa.select(
                    sa.or_(
                        sa.exists().where(HumanInterview.student_id == user.id),
                        sa.exists().where(AssessmentAssignment.student_id == user.id, AssessmentAssignment.status != "cancelled"),
                        sa.exists().where(Interview.user_id == user.id, Interview.interview_mode == "assessment"),
                    )
                )
            )
        ).scalar()
    )

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
            "has_assessments": True,
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
        return {"action": {"kind": "ai_interview", "title": f"Continue AI Interview: {assessment.track}", "detail": "Your placement assessment is ready to continue.", "href": f"/interview?interviewId={assessment.id}&interviewMode=assessment"}, "activity": activity, "has_assessments": True}

    if hub.resume is not None and hub.resume.readiness == "ready":
        saved = hub.resume
        total = saved.total_questions or 0
        return {
            "action": {
                "kind": "resume_mock",
                "title": f"Continue your {saved.track} Mock Interview",
                "detail": f"{saved.answered_questions} of {total} answers are saved." if total else "Your interview is saved.",
                "href": f"/interview?interviewId={saved.interview_id}&resumed=true&interviewMode=practice",
                "cta": "Continue",
            },
            "activity": activity,
            "has_assessments": has_assessments,
        }

    drill = (
        await session.execute(
            sa.select(CoachingAssignment)
            .join(User, User.id == CoachingAssignment.student_id)
            .where(
                CoachingAssignment.student_id == user.id,
                CoachingAssignment.status == "assigned",
                *_enrolled(CoachingAssignment.course_id),
            )
            .order_by(CoachingAssignment.due_date.nulls_last(), CoachingAssignment.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    teacher_mock = (
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
    far = dt.date.max
    drill_first = drill is not None and (
        teacher_mock is None or (drill.due_date or far) <= (teacher_mock[0].due_date or far)
    )
    if drill_first:
        param = "assignment" if drill.module in ("warm_up", "think_fast") else "coachingAssignment"
        return {
            "action": {
                "kind": "coaching_assignment",
                "title": DRILL_NAMES[drill.module],
                "detail": drill.reason,
                "href": f"{DRILL_PATHS[drill.module]}?{param}={drill.id}",
                "due_date": drill.due_date,
                "from_teacher": True,
            },
            "activity": activity,
            "has_assessments": has_assessments,
        }
    if teacher_mock is not None:
        row, name = teacher_mock
        href = f"/interview-start?mode=practice&jobProfileId={row.job_profile_id}&courseId={quote(row.course_id, safe='')}"
        return {
            "action": {
                "kind": "mock_assignment", "title": f"Mock Interview: {name}", "detail": row.reason,
                "href": href, "due_date": row.due_date, "from_teacher": True,
            },
            "activity": activity,
            "has_assessments": has_assessments,
        }

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
            if current["kind"] == "drill" and current["module"] in DRILL_PATHS:
                href = f"{DRILL_PATHS[current['module']]}?planAssignment={plan['id']}&planStep={current['id']}"
            elif current["kind"] == "mock" and current["job_profile_id"]:
                href = (
                    "/interview-start?mode=practice"
                    f"&jobProfileId={current['job_profile_id']}&courseId={quote(plan['course_id'], safe='')}"
                )
            else:
                href = "/coaching"
            return {"action": {"kind": "learning_plan", "title": plan["plan"]["title"], "detail": f"Learning Plan · Step {current['position']}: {current['title']}", "href": href}, "activity": activity, "has_assessments": has_assessments}

    if hub.focus is not None:
        return {
            "action": {"kind": "focus", "title": hub.focus.title, "detail": hub.focus.reason, "href": hub.focus.href},
            "activity": activity,
            "has_assessments": has_assessments,
        }
    done_before = (
        await session.execute(
            sa.select(sa.func.count()).select_from(Interview).where(Interview.user_id == user.id, Interview.status == "completed")
        )
    ).scalar_one() + (
        await session.execute(
            sa.select(sa.func.count()).select_from(PracticeDrillCompletion).where(PracticeDrillCompletion.user_id == user.id)
        )
    ).scalar_one()
    if not done_before:
        return {
            "action": {
                "kind": "first_win",
                "title": "Start with a two-minute warm-up",
                "detail": "There is no score. It just gets you comfortable speaking.",
                "href": "/warm-up-chat",
                "cta": "Start warm-up",
            },
            "activity": activity,
            "has_assessments": has_assessments,
        }
    return {
        "action": {
            "kind": "keep_going",
            "title": "Do one short activity today",
            "detail": "A few minutes of practice keeps your progress going.",
            "href": "/coaching",
            "cta": "Choose an activity",
        },
        "activity": activity,
        "has_assessments": has_assessments,
    }


@router.get("/feedback-time")
async def feedback_time(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
):
    """How long recent reports took, so the wait can be stated honestly. Null when unknown."""
    response.headers["Cache-Control"] = "no-store"
    since = dt.datetime.now(UTC) - dt.timedelta(days=30)
    seconds = sa.func.extract("epoch", SummaryReport.created_at - Interview.completed_at)
    value = (
        await session.execute(
            sa.select(sa.func.percentile_cont(0.5).within_group(seconds))
            .join(Interview, Interview.id == SummaryReport.interview_id)
            .where(Interview.completed_at >= since, SummaryReport.created_at >= Interview.completed_at, seconds < 3600)
        )
    ).scalar()
    return {"typical_minutes": max(1, round(float(value) / 60)) if value is not None else None}
