"""Operations recovery for administrators (architecture pack F28).

Lists what failed (email deliveries here) and offers a safe repair that never edits the
business record: a retry sends the current state of the interview, not a stale copy."""

import datetime as dt
from typing import Literal

import fastapi
import sqlalchemy as sa

from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.session import get_async_session
from src.models.db.human_interview import HumanInterview
from src.models.db.notification import NotificationDelivery
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.interview import Interview
from src.models.db.question_attempt import QuestionAttempt
from src.models.db.user import User

router = fastapi.APIRouter(prefix="/v2/operations", tags=["operations"])


def administrators_only(user):
    if not is_admin_user(user):
        raise fastapi.HTTPException(403, "Operations recovery is limited to administrators.")


@router.get("/deliveries")
async def deliveries(
    response: fastapi.Response,
    status: Literal["failed", "queued", "skipped", "sent", "superseded"] = "failed",
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    administrators_only(user)
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await session.execute(
            sa.select(NotificationDelivery, User.name, HumanInterview.title)
            .join(User, User.id == NotificationDelivery.recipient_id)
            .outerjoin(HumanInterview, HumanInterview.id == NotificationDelivery.related_id)
            .where(NotificationDelivery.status == status)
            .order_by(NotificationDelivery.created_at.desc())
            .limit(100)
        )
    ).all()
    return {
        "items": [
            {
                "id": d.id, "event": d.event, "status": d.status, "attempts": d.attempts,
                "error": d.last_error, "recipient": name, "about": title,
                "scheduled_for": d.scheduled_for, "sent_at": d.sent_at,
            }
            for d, name, title in rows
        ]
    }


@router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery(
    delivery_id: int,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    administrators_only(user)
    row = (
        await session.execute(
            sa.select(NotificationDelivery).where(NotificationDelivery.id == delivery_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Delivery not found.")
    if row.status not in ("failed", "skipped"):
        raise fastapi.HTTPException(409, "Only a failed or skipped delivery can be retried.")
    row.status, row.attempts, row.scheduled_for = "queued", 0, dt.datetime.now(dt.timezone.utc)
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action="delivery_retried", purpose="operations",
            target_type="notification_delivery", target_id=str(row.id),
            reason="An administrator queued this email to be sent again.",
            event_metadata={"event": row.event, "related_id": row.related_id},
        )
    )
    await session.commit()
    return {"id": row.id, "status": row.status}


STALLED_AFTER = dt.timedelta(minutes=10)


@router.get("/answers")
async def answers(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Saved spoken answers whose AI processing failed or stalled. Metadata only: no audio link,
    no transcript, no student name."""
    administrators_only(user)
    response.headers["Cache-Control"] = "no-store"
    now = dt.datetime.now(dt.timezone.utc)
    updated = sa.func.coalesce(
        QuestionAttempt.transcription_updated_at, QuestionAttempt.answer_saved_at, QuestionAttempt.created_at
    )
    rows = (
        await session.execute(
            sa.select(
                QuestionAttempt.id, QuestionAttempt.interview_id, QuestionAttempt.transcription_status,
                QuestionAttempt.transcription_error, updated.label("since"),
                Interview.track, Interview.interview_mode,
            )
            .join(Interview, Interview.id == QuestionAttempt.interview_id)
            .where(
                QuestionAttempt.answer_saved_at.is_not(None),
                QuestionAttempt.answer_kind == "audio",
                sa.or_(
                    QuestionAttempt.transcription_status == "failed",
                    sa.and_(
                        QuestionAttempt.transcription_status.in_(("pending", "processing")),
                        updated < now - STALLED_AFTER,
                    ),
                ),
            )
            .order_by(updated)
            .limit(100)
        )
    ).all()
    return {
        "items": [
            {
                "id": r.id, "interview_id": r.interview_id, "track": r.track, "mode": r.interview_mode,
                "state": "failed" if r.transcription_status == "failed" else "stalled",
                "since": r.since, "waiting_minutes": int((now - r.since).total_seconds() // 60),
                "reason": r.transcription_error,
            }
            for r in rows
        ]
    }


@router.post("/answers/{attempt_id}/retry")
async def retry_answer(
    attempt_id: int,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Process the same saved recording again. Never edits the answer, its score or the student's
    attempt count; only one request can reserve a given failed state, so a double click is safe."""
    from src.repository.crud.question import QuestionAttemptCRUDRepository
    from src.worker import queue

    administrators_only(user)
    found = (
        await session.execute(
            sa.select(QuestionAttempt, Interview.user_id)
            .join(Interview, Interview.id == QuestionAttempt.interview_id)
            .where(QuestionAttempt.id == attempt_id)
        )
    ).first()
    if found is None or found[0].answer_kind != "audio" or not found[0].answer_checksum:
        raise fastapi.HTTPException(404, "No saved recording to process for that answer.")
    attempt, owner_id = found
    repo = QuestionAttemptCRUDRepository(async_session=session)
    reserved = await repo.reserve_transcription_retry(
        question_attempt_id=attempt.id, user_id=owner_id, checksum=attempt.answer_checksum,
        answer_version=attempt.answer_version or 0, expected_status=attempt.transcription_status,
        expected_updated_at=attempt.transcription_updated_at,
    )
    if reserved is None:
        raise fastapi.HTTPException(409, "This answer is not waiting for recovery, or another retry already started.")
    transcript = reserved.transcription if isinstance(reserved.transcription, dict) else {}
    language = transcript.get("language", "en")
    language = language if language in {"en", "hi"} else "en"
    job_id = await queue.enqueue_job(
        "process_answer_transcription_task", question_attempt_id=attempt.id,
        checksum=attempt.answer_checksum, language=language, filename=f"answer-{attempt.id}",
    )
    if not job_id:
        await repo.fail_transcription_retry_reservation(
            question_attempt_id=attempt.id, checksum=attempt.answer_checksum,
            reserved_at=reserved.transcription_updated_at,
            error="Your answer is saved, but speech feedback could not be restarted. Select Retry speech feedback.",
        )
        raise fastapi.HTTPException(503, "The processing queue is unavailable. Try again shortly.")
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action="answer_processing_retried", purpose="operations",
            target_type="question_attempt", target_id=str(attempt.id),
            reason="An administrator restarted processing of the saved recording.",
            event_metadata={"interview_id": attempt.interview_id, "job_id": job_id},
        )
    )
    await session.commit()
    return {"id": attempt.id, "state": "queued", "job_id": job_id}
