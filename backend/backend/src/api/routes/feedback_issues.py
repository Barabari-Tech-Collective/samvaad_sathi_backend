"""Report and review transcript or feedback issues (architecture pack F11).

Students report on their own interviews (formal or mock) with an explanation; a report never
changes the saved evidence, the transcript or any score. People with interview-authoring
"update" (or administrators) work the queue and resolve with a reason; the student is told.
Reviewers see the kind, the explanation and which interview and question, not the student's name.
"""

import datetime as dt
from typing import Literal
from uuid import UUID

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator

from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.feedback_issue import FeedbackIssue
from src.models.db.interview import Interview
from src.models.db.question_attempt import QuestionAttempt
from src.services import notifications
from src.services.feature_access import permits

router = fastapi.APIRouter(prefix="/v2/feedback-issues", tags=["feedback-issues"])


def _trim(value):
    return value.strip() if isinstance(value, str) else value


class Report(BaseModel):
    request_key: UUID
    interview_id: int = Field(ge=1)
    question_attempt_id: int | None = Field(None, ge=1)
    kind: Literal["transcription", "feedback", "language", "other"]
    explanation: str = Field(min_length=10, max_length=2000)

    _trim = field_validator("explanation", mode="before")(classmethod(lambda c, v: _trim(v)))


class Resolve(BaseModel):
    decision: Literal["resolved", "dismissed"]
    note: str = Field(min_length=10, max_length=1000)

    _trim = field_validator("note", mode="before")(classmethod(lambda c, v: _trim(v)))


def reviewer(user):
    return is_admin_user(user) or (
        isinstance(user.feature_permissions, dict)
        and permits(user.feature_permissions, "interview_authoring", "update")
    )


def view(i):
    return {
        "id": i.id, "interview_id": i.interview_id, "question_attempt_id": i.question_attempt_id,
        "kind": i.kind, "explanation": i.explanation, "status": i.status,
        "resolution": i.resolution, "created_at": i.created_at, "resolved_at": i.resolved_at,
    }


@router.post("/me", status_code=201)
async def report(
    body: Report,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    existing = (
        await session.execute(
            sa.select(FeedbackIssue).where(
                FeedbackIssue.reporter_id == user.id, FeedbackIssue.request_key == str(body.request_key)
            )
        )
    ).scalar_one_or_none()
    if existing:
        response.status_code = 200
        return view(existing)
    owned = (
        await session.execute(
            sa.select(Interview.id).where(Interview.id == body.interview_id, Interview.user_id == user.id)
        )
    ).scalar_one_or_none()
    if owned is None:
        raise fastapi.HTTPException(404, "Interview not found.")
    if body.question_attempt_id is not None:
        belongs = (
            await session.execute(
                sa.select(QuestionAttempt.id).where(
                    QuestionAttempt.id == body.question_attempt_id,
                    QuestionAttempt.interview_id == body.interview_id,
                )
            )
        ).scalar_one_or_none()
        if belongs is None:
            raise fastapi.HTTPException(422, "That answer is not part of this interview.")
    row = FeedbackIssue(
        reporter_id=user.id, request_key=str(body.request_key),
        **body.model_dump(exclude={"request_key"}),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return view(row)


@router.get("/me")
async def mine(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await session.execute(
            sa.select(FeedbackIssue)
            .where(FeedbackIssue.reporter_id == user.id)
            .order_by(FeedbackIssue.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return {"items": [view(r) for r in rows]}


@router.get("")
async def queue(
    response: fastapi.Response,
    status: Literal["open", "resolved", "dismissed"] = "open",
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if not reviewer(user):
        raise fastapi.HTTPException(403, "Reviewing reported issues needs permission to edit interview content.")
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await session.execute(
            sa.select(FeedbackIssue, Interview.track, Interview.interview_mode)
            .join(Interview, Interview.id == FeedbackIssue.interview_id)
            .where(FeedbackIssue.status == status)
            .order_by(FeedbackIssue.created_at)
            .limit(100)
        )
    ).all()
    return {"items": [view(i) | {"track": track, "mode": mode} for i, track, mode in rows]}


@router.post("/{issue_id}/resolve")
async def resolve(
    issue_id: int,
    body: Resolve,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if not reviewer(user):
        raise fastapi.HTTPException(403, "Reviewing reported issues needs permission to edit interview content.")
    row = (
        await session.execute(
            sa.select(FeedbackIssue).where(FeedbackIssue.id == issue_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Issue not found.")
    if row.status != "open":
        raise fastapi.HTTPException(409, "This issue was already reviewed.")
    row.status, row.resolution = body.decision, body.note
    row.resolved_by, row.resolved_at = user.id, dt.datetime.now(dt.timezone.utc)
    notifications.notify(
        session, row.reporter_id, "feedback_issue_reviewed", "Your reported issue was reviewed",
        body.note[:200], "/history", ("feedback_issue", row.id),
    )
    await session.commit()
    return view(row)
