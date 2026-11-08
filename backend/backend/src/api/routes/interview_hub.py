"""Staff Interviews: the cross-course reads and the few writes no existing route covers."""

import datetime as dt

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator, model_validator

from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes.assessment_records import (
    ai_reachable,
    audit as release_audit,
    may_release,
)
from src.models.db.assessment_assignment import AssessmentAssignment
from src.models.db.interview import Interview
from src.models.db.notification import NotificationDelivery
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.user import User
from src.repository.crud.job_profile import JobProfileCRUDRepository
from src.services import assessment_assignments as assignments
from src.services import interview_hub as hub
from src.services import learner_hub as lh
from src.services import notifications
from src.services.interview_question_plan import unique_questions
from src.services.people import student_interview
from src.services.result_release import withheld

router = fastapi.APIRouter(prefix="/v2/interview-hub", tags=["interview-hub"])
UTC = dt.timezone.utc
MAX_RELEASE = 200
Days = fastapi.Query(7)


def check_days(days: int) -> int:
    if days not in lh.PERIODS:
        raise fastapi.HTTPException(422, "Choose a period of 1, 3, 7, 15 or 30 days.")
    return days


def no_store(response: fastapi.Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def page_size(size: int) -> int:
    if size not in hub.PAGE_SIZES:
        raise fastapi.HTTPException(422, "Show 25, 50 or 100 rows.")
    return size


@router.get("/summary")
async def summary(
    response: fastapi.Response,
    days: int = Days,
    college: str | None = None,
    course: str | None = None,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    no_store(response)
    return await hub.summary(user, session, check_days(days), college, course)


@router.get("/templates")
async def templates(
    response: fastapi.Response,
    mode: str = "assessment",
    days: int = Days,
    course: str | None = None,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if mode not in ("assessment", "practice"):
        raise fastapi.HTTPException(422, "Choose assessment or practice.")
    no_store(response)
    return await hub.templates(user, session, mode, check_days(days), course)


@router.get("/templates/{template_id}")
async def template_detail(
    template_id: int,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    no_store(response)
    return await hub.template_detail(user, session, template_id)


@router.post("/templates/{template_id}/draft")
async def ensure_draft(
    template_id: int,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """The draft to edit: the one already open, or a fresh copy of what learners get now."""
    viewer = await hub.viewer_for(user, session)
    hub._need_author(viewer)
    family = await hub.family_for(viewer, session, template_id)
    if family.draft is not None:
        if family.draft.status == "under_review":
            raise fastapi.HTTPException(409, "These questions are locked while they are being reviewed.")
        return {"id": family.draft.id, "created": False, "revision": family.draft.content_revision}
    repo = JobProfileCRUDRepository(async_session=session)
    rule = family.live.result_release
    draft = await repo.clone_profile(family.live.id, user_id=user.id, purpose=family.live.interview_mode)
    draft.result_release = rule
    await session.commit()
    return {"id": draft.id, "created": True, "revision": draft.content_revision}


@router.delete("/templates/{template_id}/draft")
async def discard_draft(
    template_id: int,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    viewer = await hub.viewer_for(user, session)
    hub._need_author(viewer)
    family = await hub.family_for(viewer, session, template_id)
    if family.draft is None:
        raise fastapi.HTTPException(404, "There is no draft to discard.")
    if family.live is None:
        raise fastapi.HTTPException(409, "This interview has never been published. Delete it instead.")
    if family.draft.status == "under_review":
        raise fastapi.HTTPException(409, "These questions are locked while they are being reviewed.")
    await JobProfileCRUDRepository(async_session=session).delete(job_profile_id=family.draft.id)
    return {"discarded": True}


class Publish(BaseModel):
    expected_revision: int | None = None


@router.post("/templates/{template_id}/publish")
async def publish(
    template_id: int,
    body: Publish,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Every difficulty must be able to fill an interview, since staff assign any of them."""
    viewer = await hub.viewer_for(user, session)
    if not viewer.publish:
        raise fastapi.HTTPException(403, "Publishing needs permission to publish interviews.")
    family = await hub.family_for(viewer, session, template_id)
    draft = family.draft
    if draft is None:
        raise fastapi.HTTPException(409, "There is nothing waiting to publish.")
    if not draft.questions_per_interview:
        raise fastapi.HTTPException(409, "Set how many questions each interview asks.")
    repo = JobProfileCRUDRepository(async_session=session)
    questions = await repo.get_job_profile_questions(job_profile_id=draft.id)
    for level, name in zip((1, 2, 3, 4), ("Easy", "Medium", "Hard", "Expert")):
        have = len(unique_questions(q for q in questions if q.level == level))
        if have < draft.questions_per_interview:
            raise fastapi.HTTPException(
                409, f"Add {draft.questions_per_interview - have} more {name} questions before publishing."
            )
    done = await repo.update_review_status(draft.id, "published", None, body.expected_revision)
    return {"id": done.id, "status": done.status}


class RequestChanges(BaseModel):
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("note", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


@router.post("/templates/{template_id}/request-changes")
async def request_changes(
    template_id: int,
    body: RequestChanges,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    viewer = await hub.viewer_for(user, session)
    if not viewer.publish:
        raise fastapi.HTTPException(403, "Reviewing needs permission to publish interviews.")
    family = await hub.family_for(viewer, session, template_id)
    if family.draft is None or family.draft.status != "under_review":
        raise fastapi.HTTPException(409, "These questions are not waiting for review.")
    repo = JobProfileCRUDRepository(async_session=session)
    await repo.update_review_status(family.draft.id, "changes_requested", body.note)
    return {"status": "changes_requested"}


class Release(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=MAX_RELEASE)
    reason: str = Field(min_length=10, max_length=1000)

    @field_validator("reason", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


@router.post("/attempts/release")
async def release_many(
    body: Release,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if not may_release(user):
        raise fastapi.HTTPException(403, "Releasing results needs permission to publish assessments.")
    rows = (
        await session.execute(
            sa.select(Interview, User)
            .join(User, User.id == Interview.user_id)
            .where(Interview.id.in_(body.ids), Interview.interview_mode == "assessment", student_interview())
            .with_for_update(of=Interview)
        )
    ).all()
    blocked = await hub._open_cases(session, [r[0].id for r in rows])
    now = dt.datetime.now(UTC)
    done, skipped = [], []
    for interview, student in rows:
        reachable = await ai_reachable(user, interview, student, session)
        if not reachable or interview.status != "completed" or not withheld(interview) or interview.id in blocked:
            skipped.append(interview.id)
            continue
        interview.result_released_at, interview.result_released_by = now, user.id
        release_audit(
            session, user, "interview", interview.id, "assessment_result_released", body.reason,
            {"student_id": interview.user_id, "course_id": interview.course_id},
        )
        notifications.notify(
            session, interview.user_id, "assessment_result_released", "Your assessment result is ready",
            "Your feedback has been reviewed and released.", "/assessments", ("interview", interview.id),
        )
        done.append(interview.id)
    await session.commit()
    lh.forget(user.id)
    found = {r[0].id for r in rows}
    return {"done": done, "skipped": skipped + [i for i in body.ids if i not in found]}


@router.get("/attempts")
async def attempts(
    response: fastapi.Response,
    of: str = "assessment",
    days: int = 7,
    all: bool = False,
    q: str | None = None,
    job: int | None = None,
    college: str | None = None,
    course: str | None = None,
    batch: str | None = None,
    difficulty: str | None = None,
    status: str | None = None,
    sort: str = "recent",
    page: int = fastapi.Query(1, ge=1),
    size: int = 25,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if of not in ("assessment", "practice"):
        raise fastapi.HTTPException(422, "Choose assessment or practice.")
    no_store(response)
    return await hub.attempts(
        user, session, of, None if all else check_days(days), q, job, college, course, batch,
        difficulty, status, sort, page, page_size(size),
    )


@router.get("/attempts/{interview_id}/detail")
async def attempt_detail(
    interview_id: int,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    no_store(response)
    return await hub.attempt_detail(user, session, interview_id)


@router.post("/attempts/{interview_id}/retry-report")
async def retry_report(
    interview_id: int,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Build a delayed report again: restart answers that got stuck, then write the report."""
    from src.api.routes.operations import STALLED_AFTER, restart_saved_answer
    from src.worker import queue

    if not is_admin_user(user):
        raise fastapi.HTTPException(403, "Retrying reports is for administrators.")
    interview = await session.get(Interview, interview_id, with_for_update=True)
    if interview is None or interview.status != "completed":
        raise fastapi.HTTPException(404, "Interview not found.")
    if (await hub._scores(session, [interview.id])).get(interview.id):
        raise fastapi.HTTPException(409, "This report is ready. There is nothing to retry.")
    if await hub._retry_requested_at(session, interview.id):
        raise fastapi.HTTPException(409, "A retry was just requested. Give it a few minutes.")
    now = dt.datetime.now(UTC)
    answers = (
        await session.execute(
            sa.select(hub.QuestionAttempt).where(
                hub.QuestionAttempt.interview_id == interview.id,
                hub.QuestionAttempt.answer_kind == "audio",
                hub.QuestionAttempt.answer_checksum.is_not(None),
                hub.QuestionAttempt.audio_url.is_not(None),
            )
        )
    ).scalars().all()
    restarted = 0
    for answer in answers:
        updated = answer.transcription_updated_at or answer.answer_saved_at or answer.created_at
        stuck = answer.transcription_status == "failed" or (
            answer.transcription_status in (None, "pending", "processing") and updated < now - STALLED_AFTER
        )
        if stuck:
            await restart_saved_answer(session, answer, interview.user_id, user)
            restarted += 1
    job_id = await queue.enqueue_job("generate_summary_report_task", interview_id=interview.id)
    if not job_id:
        await session.rollback()
        raise fastapi.HTTPException(503, "The processing queue is unavailable. Try again shortly.")
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action=hub.REPORT_RETRY, purpose="operations", target_type="interview",
            target_id=str(interview.id), reason="An administrator asked for this report to be built again.",
            event_metadata={"job_id": job_id, "answers_restarted": restarted},
        )
    )
    await session.commit()
    return {"state": "queued", "answers_restarted": restarted}


@router.get("/integrity")
async def integrity_cases(
    response: fastapi.Response,
    state: str = "open",
    case: int | None = None,
    page: int = fastapi.Query(1, ge=1),
    size: int = 25,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    no_store(response)
    return await hub.integrity_cases(user, session, state, case, page, page_size(size))


@router.get("/human")
async def human_list(
    response: fastapi.Response,
    status: str | None = None,
    q: str | None = None,
    interviewer: str | None = None,
    page: int = fastapi.Query(1, ge=1),
    size: int = 25,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    no_store(response)
    return await hub.human_list(user, session, status, q, interviewer, page, page_size(size))


@router.post("/invitations/{delivery_id}/resend")
async def resend_invitation(
    delivery_id: int,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Put a failed invitation back in the send queue, inside this person's own scope."""
    viewer = await hub.viewer_for(user, session)
    if not viewer.human_read:
        raise fastapi.HTTPException(403, "Human Interviews are not part of your role.")
    item = await session.get(NotificationDelivery, delivery_id, with_for_update=True)
    if item is None or item.related_type != "human_interview" or item.event not in ("scheduled", "rescheduled"):
        raise fastapi.HTTPException(404, "Invitation not found.")
    if not await hub._human_rows(viewer, session, hub.HumanInterview.id == item.related_id):
        raise fastapi.HTTPException(404, "Invitation not found.")
    if item.status != "failed":
        raise fastapi.HTTPException(409, "This invitation is not waiting to be resent.")
    item.status, item.last_error, item.scheduled_for = "queued", None, dt.datetime.now(UTC)
    await session.commit()
    return {"status": "queued"}


@router.get("/assignments/{key}")
async def assignment_group(
    key: str,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    no_store(response)
    return await hub.assignment_group(user, session, key)


async def _group(user, session, key, operation):
    if not assignments.permitted(user, operation):
        raise fastapi.HTTPException(403, "Changing AI Interview assignments is not part of your role.")
    viewer = await hub.viewer_for(user, session)
    profile, cohort, opens, closes, course = hub.parse_key(key)
    rows = await hub._group_query(viewer, session, profile, cohort, opens, closes, course)
    await assignments.authorize(user, course, session, operation)
    return viewer, rows


@router.post("/assignments/{key}/remind")
async def remind(
    key: str,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Tell everyone who has not started. Nobody is reminded twice in a day."""
    _, rows = await _group(user, session, key, "create")
    now = dt.datetime.now(UTC)
    since = now - dt.timedelta(hours=hub.REMIND_GAP_HOURS)
    waiting = [r[0] for r in rows if r[0].status == "assigned"]
    if rows[0][0].closes_at is not None and rows[0][0].closes_at <= now:
        raise fastapi.HTTPException(409, "This window has closed. Change the window first.")
    if now < rows[0][0].opens_at:
        raise fastapi.HTTPException(409, "This window has not opened yet.")
    recent = set(
        (
            await session.execute(
                sa.select(hub.Notification.recipient_id).where(
                    hub.Notification.recipient_id.in_([r.student_id for r in waiting] or [0]),
                    hub.Notification.kind == "learner_nudge",
                    hub.Notification.related_type == "assessment_assignment",
                    hub.Notification.created_at >= since,
                )
            )
        ).scalars()
    )
    sent, skipped = [], []
    for row in waiting:
        if row.student_id in recent:
            skipped.append(row.student_id)
            continue
        notifications.notify(
            session, row.student_id, "learner_nudge", "Your AI interview is waiting",
            "Please finish it before the window closes.", "/assessments", ("assessment_assignment", row.id),
        )
        session.add(
            NotificationDelivery(
                recipient_id=row.student_id, event="nudge", related_type="learner_nudge", related_id=row.student_id,
            )
        )
        sent.append(row.student_id)
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action="assessment_assignment_reminded", purpose="facilitator_coaching",
            target_type="assessment_assignment_group", target_id=key[:120], reason="Reminder to start.",
            event_metadata={"sent": sent, "skipped": skipped},
        )
    )
    await session.commit()
    return {"sent": len(sent), "skipped": len(skipped)}


class GroupChange(BaseModel):
    opens_at: dt.datetime
    closes_at: dt.datetime
    reason: str = Field(min_length=10, max_length=500)

    @model_validator(mode="after")
    def ordered(self):
        if self.opens_at.tzinfo is None or self.closes_at.tzinfo is None:
            raise ValueError("Give times with a timezone.")
        if self.closes_at <= self.opens_at:
            raise ValueError("The window must close after it opens.")
        return self


class GroupCancel(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


def _assignment_audit(session, user, row, action, reason):
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action=action, purpose="assessment_assignment",
            target_type="assessment_assignment", target_id=str(row.id), reason=reason,
            event_metadata={"student_id": row.student_id, "course_id": row.course_id},
        )
    )


@router.post("/assignments/{key}/change")
async def change_window(
    key: str,
    body: GroupChange,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if body.closes_at <= dt.datetime.now(UTC):
        raise fastapi.HTTPException(422, "The window must end in the future.")
    _, rows = await _group(user, session, key, "update")
    changed: list[AssessmentAssignment] = []
    for r in rows:
        row = r[0]
        if row.status != "assigned":
            continue
        row.opens_at, row.closes_at, row.version = body.opens_at, body.closes_at, row.version + 1
        _assignment_audit(session, user, row, "assessment_assignment_rescheduled", body.reason)
        notifications.notify(
            session, row.student_id, "assessment_rescheduled", "Your AI interview time changed",
            "Open it to see the new window.", "/assessments", ("assessment_assignment", row.id),
        )
        changed.append(row)
    if not changed:
        raise fastapi.HTTPException(409, "Everyone in this group has already started.")
    await session.commit()
    return {"key": hub.group_key(changed[0]), "changed": len(changed)}


@router.post("/assignments/{key}/cancel")
async def cancel_group(
    key: str,
    body: GroupCancel,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    _, rows = await _group(user, session, key, "update")
    now, count = dt.datetime.now(UTC), 0
    for r in rows:
        row = r[0]
        if row.status != "assigned":
            continue
        row.status, row.version, row.closed_at = "cancelled", row.version + 1, now
        _assignment_audit(session, user, row, "assessment_assignment_cancelled", body.reason)
        count += 1
    if not count:
        raise fastapi.HTTPException(409, "Everyone in this group has already started.")
    await session.commit()
    return {"cancelled": count}

