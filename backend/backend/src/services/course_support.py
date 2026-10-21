"""Course-scoped practice support signals, not cheating or recovery decisions."""

import datetime
import sqlalchemy as sa
from src.models.db.interview import Interview
from src.models.db.user import User
from src.models.db.summary_report import SummaryReport
from src.models.db.question_attempt import QuestionAttempt
from src.services.feature_access import permits
from src.api.dependencies.admin import is_admin_user


def allowed(user):
    return is_admin_user(user) or (
        isinstance(user.feature_permissions, dict)
        and permits(user.feature_permissions, "practice_support", "read")
    )


async def can_open(user, course_id, session):
    if not allowed(user):
        return False
    if is_admin_user(user):
        return True
    from src.api.dependencies.admin import get_user_active_memberships
    from src.api.routes.practice_dashboard import resolve_dashboard_role
    from src.services.course_access import membership_grants

    memberships = await get_user_active_memberships(user.id, session)
    return any(
        resolve_dashboard_role(user, [item]) == "facilitator"
        and any(course_id in grant.course_ids for grant in membership_grants([item]))
        for item in memberships
    )


def support_query(course_id, authorized):
    now = datetime.datetime.now(datetime.timezone.utc)
    report = sa.exists(
        sa.select(SummaryReport.id).where(
            SummaryReport.interview_id == Interview.id,
            sa.func.jsonb_typeof(SummaryReport.report_json) == "object",
            SummaryReport.report_json != {},
        )
    )
    failed = (
        sa.select(sa.func.count(QuestionAttempt.id))
        .where(
            QuestionAttempt.interview_id == Interview.id,
            QuestionAttempt.transcription_status == "failed",
        )
        .correlate(Interview)
        .scalar_subquery()
    )
    # Report and open-age thresholds are signals for a person to investigate.
    # The model lacks a reliable last-progress timestamp, so do not call it stuck.
    issue = sa.case(
        (
            sa.or_(
                Interview.status.in_(["failed", "terminated"]),
                Interview.proctoring_terminated.is_(True),
            ),
            "session_blocked",
        ),
        (
            sa.and_(
                Interview.status == "completed",
                ~report,
                sa.func.coalesce(Interview.completed_at, Interview.created_at)
                < now - datetime.timedelta(minutes=15),
            ),
            "report_delayed",
        ),
        (
            sa.and_(
                Interview.status.in_(["active", "paused", "in_progress"]),
                Interview.created_at < now - datetime.timedelta(hours=2),
            ),
            "open_over_two_hours",
        ),
        (failed > 0, "answer_processing_failed"),
        else_=None,
    )
    return (
        sa.select(
            Interview.id.label("interview_id"),
            Interview.user_id.label("student_id"),
            Interview.status,
            Interview.created_at,
            Interview.completed_at,
            issue.label("issue"),
            failed.label("failed_answers"),
        )
        .join(User, User.id == Interview.user_id)
        .where(
            Interview.course_id == course_id,
            Interview.interview_mode == "practice",
            authorized,
        )
        .subquery()
    )
