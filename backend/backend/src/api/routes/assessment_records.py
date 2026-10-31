"""One assessment record for staff (architecture pack sec 3, "One assessment detail page").

An assessment is either an AI interview taken in assessment mode or a human interview. Both
are read here in one shape: briefing and schedule, participants, execution status, result and
feedback, and the history this person may see. A learner page, an opening, the Overview and
the interviewer's list all link to this same record. Student-visible feedback and internal
reasons are separate fields. Access follows the rules of the page each record came from.
"""

import datetime as dt
from typing import Literal

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator

from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes.learners import allowed, college_scope_allows
from src.api.routes.human_interviews import staff_row
from src.models.db.human_interview import HumanInterview
from src.models.db.integrity_case import IntegrityCase
from src.models.db.interview import Interview
from src.models.db.job_profile import JobProfile
from src.models.db.opportunity import Opportunity
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.report import Report
from src.models.db.summary_report import SummaryReport
from src.models.db.user import User
from src.services import human_interviews as interviews
from src.services import integrity_review
from src.services.analytics import (
    _extract_knowledge_score,
    _extract_overall_score,
    _extract_speech_score,
)
from src.services.course_access import grants_for, student_predicate
from src.services.feature_access import permits
from src.services.people import student_interview, student_only
from src.services.result_release import withheld

router = fastapi.APIRouter(prefix="/v2/assessment-records", tags=["assessment-records"])

NOT_FOUND = "Assessment not found within your access."

RELEASE_RULES = {
    "immediate": "Shown to the student as soon as scoring finishes.",
    "review": "Held until someone with permission to publish assessments releases it.",
}


class Reasoned(BaseModel):
    reason: str = Field(min_length=10, max_length=1000)

    @field_validator("reason", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class ReleaseRule(Reasoned):
    result_release: Literal["immediate", "review"]


def may_release(user):
    """Releasing a held AI result belongs to people who may publish assessments."""
    return is_admin_user(user) or (
        isinstance(user.feature_permissions, dict)
        and permits(user.feature_permissions, "interview_authoring", "publish")
    )


def audit(session, user, target_type, target_id, action, reason, details):
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id,
            action=action,
            purpose="assessment_release",
            target_type=target_type,
            target_id=str(target_id),
            reason=reason,
            event_metadata=details,
        )
    )


async def ai_history(session, interview_id):
    rows = (
        await session.execute(
            sa.select(PracticeDashboardAuditEvent, User.name)
            .outerjoin(User, User.id == PracticeDashboardAuditEvent.actor_id)
            .where(
                PracticeDashboardAuditEvent.target_type == "interview",
                PracticeDashboardAuditEvent.target_id == str(interview_id),
            )
            .order_by(PracticeDashboardAuditEvent.id)
        )
    ).all()
    return [
        {"action": e.action, "by": name, "at": e.created_at, "reason": e.reason}
        for e, name in rows
    ]


async def next_held(session, after_id):
    """The oldest other result still waiting, so a reviewer can move straight on."""
    return (
        await session.execute(
            sa.select(Interview.id)
            .where(
                Interview.interview_mode == "assessment",
                Interview.result_release == "review",
                Interview.result_released_at.is_(None),
                Interview.status == "completed",
                Interview.id != after_id,
                student_interview(),
            )
            .order_by(Interview.completed_at.asc().nullslast(), Interview.id)
            .limit(1)
        )
    ).scalar_one_or_none()

# Truthful, small statuses (pack sec 12).
AI_STATUS = {
    "active": "In progress",
    "paused": "Paused",
    "completed": "Completed",
    "expired": "Expired before finishing",
    "failed": "Could not be scored",
}


def human_status(row, now):
    if row.result:
        return "Outcome recorded"
    if row.status == "scheduled":
        return "Awaiting attendance update" if now >= row.ends_at else "Scheduled"
    return {
        "completed": "Attended · awaiting outcome",
        "cancelled": "Cancelled",
        "no_show": "Student did not attend",
        "technical_issue": "Technical issue",
    }.get(row.status, row.status)


def person(row):
    return {"id": row.id, "name": row.name, "college": row.university}


async def opening(session, opportunity_id):
    if opportunity_id is None:
        return None
    o = await session.get(Opportunity, opportunity_id)
    return o and {"id": o.id, "title": o.title, "organisation": o.organisation}


async def ai_reachable(user, interview, student, session):
    """Analytics reach (with its college rule), or a course grant covering this sitting."""
    if allowed(user, "analytics") and await college_scope_allows(user, student, session):
        return True
    if not interview.course_id:
        return False
    admin = is_admin_user(user)
    grants = () if admin else await grants_for(user, session)
    return (
        await session.execute(
            sa.select(User.id).where(
                User.id == student.id,
                student_predicate(interview.course_id, grants, global_access=admin),
            )
        )
    ).scalar_one_or_none() is not None


async def integrity(user, interview, session):
    """Integrity cases on this sitting, only for people the integrity review lets in."""
    if not interview.course_id:
        return None
    try:
        predicate = await integrity_review.scope(user, interview.course_id, session, "read")
    except fastapi.HTTPException:
        return None
    rows = (
        await session.execute(
            sa.select(IntegrityCase)
            .where(IntegrityCase.interview_id == interview.id, predicate)
            .order_by(IntegrityCase.id)
        )
    ).scalars()
    return [
        {
            "id": c.id,
            "state": c.state,
            "outcome": c.outcome,
            "appeal_deadline": c.appeal_deadline,
            "href": f"/dashboard/samvaad/integrity-review?course={c.course_id}&case={c.id}",
        }
        for c in rows
    ]


@router.get("/ai/{interview_id}")
async def ai_record(
    interview_id: int,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    row = (
        await session.execute(
            sa.select(Interview, User, Report, SummaryReport)
            .join(User, User.id == Interview.user_id)
            .outerjoin(Report, Report.interview_id == Interview.id)
            .outerjoin(SummaryReport, SummaryReport.interview_id == Interview.id)
            .where(
                Interview.id == interview_id,
                Interview.interview_mode == "assessment",
                student_interview(),
            )
        )
    ).first()
    if row is None:
        raise fastapi.HTTPException(404, NOT_FOUND)
    interview, student, report, summary = row
    if not await ai_reachable(user, interview, student, session):
        raise fastapi.HTTPException(404, NOT_FOUND)
    profile = (
        await session.get(JobProfile, interview.job_profile_id)
        if interview.job_profile_id
        else None
    )
    overall = _extract_overall_score(report, summary)
    scored = interview.status == "completed" and overall is not None
    return {
        "kind": "ai",
        "id": interview.id,
        "status": interview.status,
        "status_label": AI_STATUS.get(interview.status, interview.status.replace("_", " ").capitalize()),
        "briefing": {
            "title": profile.job_name if profile else interview.track,
            "role": interview.track,
            "difficulty": interview.difficulty,
            "course_id": interview.course_id,
            "instructions": None,
            "opportunity": None,
        },
        "schedule": {
            "starts_at": interview.started_at or interview.created_at,
            "ends_at": interview.completed_at,
            "expires_at": interview.expires_at,
            "duration_seconds": interview.duration_seconds,
        },
        "participants": {
            "student": person(student),
            "interviewer": {"name": "AI interviewer", "is_you": False},
        },
        "execution": {
            "proctoring_stopped": bool(interview.proctoring_terminated),
            "proctoring_reason": interview.proctoring_violation_reason,
            "reschedule_count": None,
            "close_reason": None,
        },
        "result": {
            "label": f"{round(overall)} / 100" if scored else None,
            "scores": {
                "overall": overall,
                "speech": _extract_speech_score(report, summary),
                "knowledge": _extract_knowledge_score(report, summary),
            }
            if scored
            else None,
            # AI feedback reaches the student when scoring finishes; nothing holds it back.
            "released": scored and not withheld(interview),
            "released_at": interview.result_released_at,
            "held": interview.status == "completed" and withheld(interview),
            "release_rule": RELEASE_RULES[interview.result_release],
        },
        "student_feedback": None,
        "integrity": await integrity(user, interview, session),
        "history": await ai_history(session, interview.id),
        "can_release": interview.status == "completed"
        and withheld(interview)
        and may_release(user),
        "next_held": await next_held(session, interview.id) if may_release(user) else None,
        "links": {"scores": f"/dashboard/samvaad/interviews/{interview.id}"},
    }


@router.get("/human/{appointment_id}")
async def human_record(
    appointment_id: int,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    course_id = (
        await session.execute(
            sa.select(HumanInterview.course_id).where(HumanInterview.id == appointment_id)
        )
    ).scalar_one_or_none()
    if course_id is None or not interviews.permitted(user, "read"):
        raise fastapi.HTTPException(404, NOT_FOUND)
    try:
        # Same rule as the interviews list: course scope, and only admins see others' rounds.
        row = await staff_row(
            session, user, course_id, appointment_id, "read", historical=True
        )
    except fastapi.HTTPException as error:
        raise fastapi.HTTPException(404, NOT_FOUND) from error
    student = (
        await session.execute(sa.select(User).where(User.id == row.student_id, student_only()))
    ).scalar_one()
    host = await session.get(User, row.host_id)
    manage = is_admin_user(user) or interviews.permitted(user, "update")
    history = None
    if manage:
        events = (
            await session.execute(
                sa.select(PracticeDashboardAuditEvent, User.name)
                .outerjoin(User, User.id == PracticeDashboardAuditEvent.actor_id)
                .where(
                    PracticeDashboardAuditEvent.target_type == "human_interview",
                    PracticeDashboardAuditEvent.target_id == str(row.id),
                )
                .order_by(PracticeDashboardAuditEvent.id)
            )
        ).all()
        history = [
            {
                "action": e.action,
                "by": name,
                "at": e.created_at,
                # Internal: never shown to the student.
                "reason": e.reason,
            }
            for e, name in events
        ]
    released = row.result_released_at is not None
    return {
        "kind": "human",
        "id": row.id,
        "status": row.status,
        "status_label": human_status(row, dt.datetime.now(dt.timezone.utc)),
        "briefing": {
            "title": row.title,
            "role": row.role_name,
            "difficulty": None,
            "course_id": row.course_id,
            "instructions": row.instructions,
            "opportunity": await opening(session, row.opportunity_id),
        },
        "schedule": {
            "starts_at": row.starts_at,
            "ends_at": row.ends_at,
            "expires_at": None,
            "duration_seconds": None,
        },
        "participants": {
            "student": person(student),
            "interviewer": {
                "name": row.interviewer_name or (host.name if host else None),
                "is_you": row.host_id == user.id,
            },
        },
        "execution": {
            "proctoring_stopped": None,
            "proctoring_reason": None,
            "reschedule_count": row.reschedule_count,
            # Internal reason for closing; the student is not shown it.
            "close_reason": row.close_reason,
        },
        "result": {
            "label": row.result,
            "scores": None,
            "released": released,
            "released_at": row.result_released_at,
            "release_rule": "The interviewer releases the outcome to the student.",
        },
        # What the student sees once released, kept apart from internal reasons.
        "student_feedback": row.feedback,
        "integrity": None,
        "history": history,
        "links": {
            "manage": f"/dashboard/samvaad/human-interviews?course={row.course_id}",
        },
    }


@router.post("/ai/{interview_id}/release")
async def release_ai_result(
    interview_id: int,
    body: Reasoned,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    row = (
        await session.execute(
            sa.select(Interview, User)
            .join(User, User.id == Interview.user_id)
            .where(
                Interview.id == interview_id,
                Interview.interview_mode == "assessment",
                student_interview(),
            )
            .with_for_update(of=Interview)
        )
    ).first()
    if row is None or not await ai_reachable(user, row[0], row[1], session):
        raise fastapi.HTTPException(404, NOT_FOUND)
    interview = row[0]
    if not may_release(user):
        raise fastapi.HTTPException(
            403, "Releasing results needs permission to publish assessments."
        )
    if interview.status != "completed" or not withheld(interview):
        raise fastapi.HTTPException(409, "This result is not waiting to be released.")
    now = dt.datetime.now(dt.timezone.utc)
    interview.result_released_at, interview.result_released_by = now, user.id
    audit(
        session, user, "interview", interview.id, "assessment_result_released",
        body.reason, {"student_id": interview.user_id, "course_id": interview.course_id},
    )
    await session.commit()
    return {"id": interview.id, "released_at": now}


@router.put("/templates/{job_profile_id}/release-rule")
async def set_release_rule(
    job_profile_id: int,
    body: ReleaseRule,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if not (
        is_admin_user(user)
        or (
            isinstance(user.feature_permissions, dict)
            and permits(user.feature_permissions, "interview_authoring", "update")
        )
    ):
        raise fastapi.HTTPException(403, "Changing this needs permission to edit templates.")
    profile = (
        await session.execute(
            sa.select(JobProfile).where(JobProfile.id == job_profile_id).with_for_update()
        )
    ).scalar_one_or_none()
    if profile is None or profile.interview_mode != "assessment":
        raise fastapi.HTTPException(404, "Assessment template not found.")
    previous = profile.result_release
    profile.result_release = body.result_release
    # Applies to sittings that start from now on; started ones keep the rule they began with.
    audit(
        session, user, "job_profile", profile.id, "assessment_release_rule_changed",
        body.reason, {"from": previous, "to": body.result_release},
    )
    await session.commit()
    return {"id": profile.id, "result_release": profile.result_release}


@router.get("/templates/{job_profile_id}/release-rule")
async def get_release_rule(
    job_profile_id: int,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    profile = await session.get(JobProfile, job_profile_id)
    if profile is None or profile.interview_mode != "assessment":
        raise fastapi.HTTPException(404, "Assessment template not found.")
    return {
        "id": profile.id,
        "result_release": profile.result_release,
        "explanation": RELEASE_RULES[profile.result_release],
        "can_change": is_admin_user(user)
        or (
            isinstance(user.feature_permissions, dict)
            and permits(user.feature_permissions, "interview_authoring", "update")
        ),
    }
