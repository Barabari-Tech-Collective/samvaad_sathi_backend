"""Human interview scorecard, review and release (architecture pack F20).

Interviewer (the host or the assigned interviewer, with human_interviews "update"):
  draft -> submit.
Reviewer (human_interviews "review", or an administrator; never the person who submitted it
unless an administrator): approve or return with a reason.
Release (reviewer): shares the approved result and the student-visible comments. Internal notes
and the reviewer's note are never sent to the student. Once a scorecard exists, the quick
outcome form is refused so a result cannot bypass review.
"""

import datetime as dt
from typing import Literal

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator

from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes.human_interviews import email_payload, queue_email, staff_row
from src.models.db.human_interview import HumanInterview
from src.models.db.human_scorecard import HumanScorecard
from src.models.db.user import User
from src.services import human_interviews as service
from src.services.feature_access import permits

router = fastapi.APIRouter(prefix="/v2/human-interviews", tags=["human-scorecards"])

UTC = dt.timezone.utc
RESULTS = Literal["selected", "next_round", "on_hold", "not_selected"]
DEFAULT_CRITERIA = ["Communication", "Technical depth", "Problem solving", "Fit for the role"]


def _trim(value):
    return value.strip() if isinstance(value, str) else value


class Criterion(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    score: int | None = Field(None, ge=0, le=5)
    evidence: str | None = Field(None, max_length=1000)

    _trim = field_validator("name", "evidence", mode="before")(classmethod(lambda c, v: _trim(v)))


class Draft(BaseModel):
    expected_version: int = Field(ge=0)
    criteria: list[Criterion] = Field(min_length=1, max_length=12)
    suggested_result: RESULTS | None = None
    internal_notes: str | None = Field(None, max_length=3000)
    student_comments: str | None = Field(None, max_length=3000)

    _trim = field_validator("internal_notes", "student_comments", mode="before")(
        classmethod(lambda c, v: _trim(v) or None)
    )


class Versioned(BaseModel):
    expected_version: int = Field(ge=1)


class Review(Versioned):
    decision: Literal["approve", "return"]
    note: str = Field(min_length=10, max_length=1000)

    _trim = field_validator("note", mode="before")(classmethod(lambda c, v: _trim(v)))


def can_review(user):
    return is_admin_user(user) or (
        isinstance(user.feature_permissions, dict)
        and permits(user.feature_permissions, "human_interviews", "review")
    )


def view(card, row, user):
    mine = user.id in (row.host_id, row.interviewer_id)
    status = card.status if card else "none"
    return {
        "appointment_id": row.id,
        "exists": card is not None,
        "status": status,
        "version": card.version if card else 0,
        "criteria": card.criteria if card else [{"name": n, "score": None, "evidence": None} for n in DEFAULT_CRITERIA],
        "suggested_result": card.suggested_result if card else None,
        "internal_notes": card.internal_notes if card else None,
        "student_comments": card.student_comments if card else None,
        "submitted_at": card.submitted_at if card else None,
        "reviewed_at": card.reviewed_at if card else None,
        "review_note": card.review_note if card else None,
        "released": row.result_released_at is not None,
        "can_edit": (
            mine
            and service.permitted(user, "update")
            and row.status == "completed"
            and row.result_released_at is None
            and status in ("none", "draft", "returned")
        ),
        "can_submit": (
            mine
            and service.permitted(user, "update")
            and status in ("draft", "returned")
        ),
        "can_review": (
            can_review(user)
            and status == "submitted"
            and (is_admin_user(user) or card.submitted_by != user.id)
        ),
        "can_release": can_review(user) and status == "approved" and row.result_released_at is None,
    }


async def load(session, user, course_id, appointment_id, lock=False):
    row = await staff_row(
        session, user, course_id, appointment_id, "read", lock=lock, historical=True
    )
    stmt = sa.select(HumanScorecard).where(HumanScorecard.appointment_id == row.id)
    if lock:
        stmt = stmt.with_for_update()
    return row, (await session.execute(stmt)).scalar_one_or_none()


@router.get("/courses/{course_id}/{appointment_id}/scorecard")
async def get_scorecard(
    course_id: str,
    appointment_id: int,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    # A reviewer who is neither host nor interviewer still needs to read what was submitted.
    if can_review(user) and not is_admin_user(user):
        row = (
            await session.execute(
                sa.select(HumanInterview).where(
                    HumanInterview.id == appointment_id, HumanInterview.course_id == course_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise fastapi.HTTPException(404, "Human interview not found within your current access.")
        await service.authorize(user, course_id, session, "read", historical=True)
        card = (
            await session.execute(
                sa.select(HumanScorecard).where(HumanScorecard.appointment_id == row.id)
            )
        ).scalar_one_or_none()
    else:
        row, card = await load(session, user, course_id, appointment_id)
    return view(card, row, user)


@router.put("/courses/{course_id}/{appointment_id}/scorecard")
async def save_draft(
    course_id: str,
    appointment_id: int,
    body: Draft,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    row, card = await load(session, user, course_id, appointment_id, lock=True)
    state = view(card, row, user)
    if not state["can_edit"]:
        raise fastapi.HTTPException(
            409,
            "The interviewer can edit the scorecard after marking the interview as completed, "
            "until it is submitted or its result is shared.",
        )
    if body.expected_version != state["version"]:
        raise fastapi.HTTPException(409, "This scorecard changed. Refresh before saving.")
    names = [c.name.casefold() for c in body.criteria]
    if len(set(names)) != len(names):
        raise fastapi.HTTPException(422, "Each criterion needs a different name.")
    fields = dict(
        criteria=[c.model_dump() for c in body.criteria],
        suggested_result=body.suggested_result,
        internal_notes=body.internal_notes,
        student_comments=body.student_comments,
    )
    if card is None:
        card = HumanScorecard(appointment_id=row.id, status="draft", version=1, **fields)
        session.add(card)
    else:
        for key, value in fields.items():
            setattr(card, key, value)
        card.status, card.version = "draft", card.version + 1
    service.audit(session, user, row, "human_scorecard_saved", "Saved a draft scorecard.")
    await session.commit()
    await session.refresh(card)
    return view(card, row, user)


@router.post("/courses/{course_id}/{appointment_id}/scorecard/submit")
async def submit(
    course_id: str,
    appointment_id: int,
    body: Versioned,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    row, card = await load(session, user, course_id, appointment_id, lock=True)
    state = view(card, row, user)
    if card is None or not state["can_submit"]:
        raise fastapi.HTTPException(409, "Save a draft scorecard first.")
    if body.expected_version != card.version:
        raise fastapi.HTTPException(409, "This scorecard changed. Refresh before submitting.")
    scored = [c for c in card.criteria if c.get("score") is not None]
    if not scored or not card.suggested_result or not card.student_comments:
        raise fastapi.HTTPException(
            422,
            "Score at least one criterion, choose a suggested result and write the comments "
            "the student will see.",
        )
    card.status, card.version = "submitted", card.version + 1
    card.submitted_by, card.submitted_at = user.id, dt.datetime.now(UTC)
    card.review_note = None
    service.audit(session, user, row, "human_scorecard_submitted", "Submitted for review.")
    await session.commit()
    await session.refresh(card)
    return view(card, row, user)


@router.post("/courses/{course_id}/{appointment_id}/scorecard/review")
async def review(
    course_id: str,
    appointment_id: int,
    body: Review,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if not can_review(user):
        raise fastapi.HTTPException(403, "Reviewing scorecards needs the review permission.")
    await service.authorize(user, course_id, session, "read", historical=True)
    row = (
        await session.execute(
            sa.select(HumanInterview)
            .where(HumanInterview.id == appointment_id, HumanInterview.course_id == course_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    card = (
        await session.execute(
            sa.select(HumanScorecard)
            .where(HumanScorecard.appointment_id == appointment_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or card is None:
        raise fastapi.HTTPException(404, "Scorecard not found.")
    if card.status != "submitted":
        raise fastapi.HTTPException(409, "Only a submitted scorecard can be reviewed.")
    if body.expected_version != card.version:
        raise fastapi.HTTPException(409, "This scorecard changed. Refresh before reviewing.")
    if card.submitted_by == user.id and not is_admin_user(user):
        raise fastapi.HTTPException(403, "Someone other than the interviewer reviews the scorecard.")
    card.status = "approved" if body.decision == "approve" else "returned"
    card.reviewed_by, card.reviewed_at, card.review_note = user.id, dt.datetime.now(UTC), body.note
    card.version += 1
    service.audit(
        session, user, row,
        "human_scorecard_approved" if body.decision == "approve" else "human_scorecard_returned",
        body.note,
    )
    await session.commit()
    await session.refresh(card)
    return view(card, row, user)


@router.post("/courses/{course_id}/{appointment_id}/scorecard/release")
async def release(
    course_id: str,
    appointment_id: int,
    body: Versioned,
    background: fastapi.BackgroundTasks,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Share the approved result and the student-visible comments. Explicit, separate from approval."""
    if not can_review(user):
        raise fastapi.HTTPException(403, "Releasing a result needs the review permission.")
    await service.authorize(user, course_id, session, "read", historical=True)
    row = (
        await session.execute(
            sa.select(HumanInterview)
            .where(HumanInterview.id == appointment_id, HumanInterview.course_id == course_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    card = (
        await session.execute(
            sa.select(HumanScorecard).where(HumanScorecard.appointment_id == appointment_id)
        )
    ).scalar_one_or_none()
    if row is None or card is None or card.status != "approved":
        raise fastapi.HTTPException(409, "Only an approved scorecard can be released.")
    if row.result_released_at is not None:
        raise fastapi.HTTPException(409, "This result was already shared with the student.")
    if body.expected_version != card.version:
        raise fastapi.HTTPException(409, "This scorecard changed. Refresh before releasing.")
    row.result, row.feedback = card.suggested_result, card.student_comments
    row.result_released_at = dt.datetime.now(UTC)
    row.version += 1
    service.audit(session, user, row, "human_interview_result_released", "Released the approved scorecard.")
    payload = await email_payload(session, row)
    await session.commit()
    queue_email(background, "result", payload)
    await session.refresh(card)
    return view(card, row, user)


@router.get("/courses/{course_id}/interviewers")
async def interviewers(
    course_id: str,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Staff who may take a human interview in this course, to pick when scheduling. Names only."""
    from src.models.db.team_member import TeamMember

    response.headers["Cache-Control"] = "no-store"
    await service.authorize(user, course_id, session, "create")
    people = (
        await session.execute(
            sa.select(User.id, User.name, User.feature_permissions, User.is_admin, User.is_super_admin)
            .where(
                User.id != user.id,
                sa.or_(
                    User.is_admin.is_(True),
                    User.is_super_admin.is_(True),
                    sa.exists(
                        sa.select(TeamMember.id).where(
                            TeamMember.user_id == User.id,
                            TeamMember.status == "active",
                            TeamMember.course_ids.contains([course_id]),
                        )
                    ),
                ),
            )
            .order_by(User.name)
            .limit(100)
        )
    ).all()
    return {
        "items": [
            {"id": p.id, "name": p.name}
            for p in people
            if p.is_admin or p.is_super_admin
            or (isinstance(p.feature_permissions, dict)
                and permits(p.feature_permissions, "human_interviews", "update"))
        ]
    }
