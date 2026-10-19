"""Transactional follow-up lifecycle against the isolated PostgreSQL database."""

import asyncio
import datetime
from uuid import uuid4
from unittest.mock import AsyncMock

import fastapi
import pytest
import sqlalchemy as sa

from .test_fixed_question_rounds import db, pytestmark  # noqa: F401
from src.api.routes import coaching_assignments as routes
from src.models.db.coaching_assignment import CoachingAssignment
from src.models.db.practice_drill_completion import PracticeDrillCompletion
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.user import User
from src.models.schemas.interview import CompletePracticeDrillRequest
from src.services.practice_drill_completion import record_practice_drill_completion


def payload(assignment_id, receipt="warmup:receipt001", module="warm_up"):
    return CompletePracticeDrillRequest(
        assignment_id=assignment_id,
        module=module,
        level="beginner",
        client_completion_id=receipt,
        evidence_kind="audio",
        evidence_units=512,
    )


async def seed(factory, student_id):
    async with factory() as session:
        student = await session.get(User, student_id)
        student.enrolled_course_ids = ["course-a"]
        row = CoachingAssignment(
            student_id=student_id,
            assigned_by=student_id,
            course_id="course-a",
            module="warm_up",
            reason="Practise opening answers",
            status="assigned",
            version=1,
            request_key=str(uuid4()),
            request_hash="a" * 64,
        )
        session.add(row)
        await session.commit()
        return row.id


@pytest.mark.asyncio
async def test_completion_is_atomic_idempotent_and_owner_bound(db):
    _, factory, student_id = db
    assignment_id = await seed(factory, student_id)
    async with factory() as session:
        with pytest.raises(fastapi.HTTPException) as error:
            await record_practice_drill_completion(
                session, user_id=student_id + 500, payload=payload(assignment_id)
            )
        assert error.value.status_code == 404
        await session.rollback()
        first, created = await record_practice_drill_completion(
            session, user_id=student_id, payload=payload(assignment_id)
        )
        assert created
        second, created = await record_practice_drill_completion(
            session, user_id=student_id, payload=payload(assignment_id)
        )
        assert not created and first.id == second.id
        row = await session.get(CoachingAssignment, assignment_id)
        assert (
            row.status == "completed"
            and row.version == 2
            and row.completion_id == first.id
        )
        assert (
            await session.execute(
                sa.select(sa.func.count()).select_from(PracticeDrillCompletion)
            )
        ).scalar_one() == 1
        assert (
            await session.execute(
                sa.select(sa.func.count()).select_from(PracticeDashboardAuditEvent)
            )
        ).scalar_one() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancelled", "revoked", "module", "reused"])
async def test_invalid_completion_does_not_close_assignment(db, failure):
    _, factory, student_id = db
    assignment_id = await seed(factory, student_id)
    async with factory() as session:
        row = await session.get(CoachingAssignment, assignment_id)
        if failure == "cancelled":
            row.status = "cancelled"
        if failure == "revoked":
            (await session.get(User, student_id)).enrolled_course_ids = []
        if failure == "reused":
            await record_practice_drill_completion(
                session, user_id=student_id, payload=payload(None)
            )
        await session.commit()
        with pytest.raises(fastapi.HTTPException) as error:
            await record_practice_drill_completion(
                session,
                user_id=student_id,
                payload=payload(
                    assignment_id,
                    module="think_fast" if failure == "module" else "warm_up",
                ),
            )
        assert (
            error.value.status_code
            == {"cancelled": 409, "revoked": 403, "module": 422, "reused": 409}[failure]
        )
        await session.rollback()
        row = await session.get(CoachingAssignment, assignment_id)
        assert row.status == ("cancelled" if failure == "cancelled" else "assigned")
        assert row.completion_id is None


@pytest.mark.asyncio
async def test_concurrent_completions_create_one_receipt(db):
    _, factory, student_id = db
    assignment_id = await seed(factory, student_id)

    async def finish():
        async with factory() as session:
            return await record_practice_drill_completion(
                session, user_id=student_id, payload=payload(assignment_id)
            )

    results = await asyncio.gather(finish(), finish())
    assert sorted(created for _, created in results) == [False, True]
    assert results[0][0].id == results[1][0].id


@pytest.mark.asyncio
async def test_creation_retry_conflict_and_cancel_version(db, monkeypatch):
    _, factory, student_id = db
    monkeypatch.setattr(routes, "authorize_staff", AsyncMock())
    async with factory() as session:
        user = await session.get(User, student_id)
        body = routes.AssignmentCreate(
            request_key=uuid4(),
            module="warm_up",
            reason="Prepare a clearer introduction",
        )
        first = await routes.create_assignment(
            "course-a", student_id, body, fastapi.Response(), user, session
        )
        response = fastapi.Response()
        retry = await routes.create_assignment(
            "course-a", student_id, body, response, user, session
        )
        assert first["id"] == retry["id"] and response.status_code == 200
        with pytest.raises(fastapi.HTTPException) as error:
            await routes.create_assignment(
                "course-a",
                student_id,
                body.model_copy(update={"module": "think_fast"}),
                fastapi.Response(),
                user,
                session,
            )
        assert error.value.status_code == 409
        await session.rollback()
        user = await session.get(User, student_id)
        cancelled = await routes.cancel_assignment(
            "course-a",
            student_id,
            first["id"],
            routes.AssignmentCancel(
                expected_version=1, reason="Replaced by another coaching plan"
            ),
            user,
            session,
        )
        assert cancelled["status"] == "cancelled" and cancelled["version"] == 2
        with pytest.raises(fastapi.HTTPException) as error:
            await routes.cancel_assignment(
                "course-a",
                student_id,
                first["id"],
                routes.AssignmentCancel(
                    expected_version=1, reason="Replaced by another coaching plan"
                ),
                user,
                session,
            )
        assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_cancel_and_complete_have_only_one_winner(db, monkeypatch):
    _, factory, student_id = db
    assignment_id = await seed(factory, student_id)
    monkeypatch.setattr(routes, "authorize_staff", AsyncMock())

    async def finish():
        async with factory() as session:
            try:
                await record_practice_drill_completion(
                    session, user_id=student_id, payload=payload(assignment_id)
                )
                return "completed"
            except fastapi.HTTPException as error:
                assert error.status_code == 409
                return "conflict"

    async def cancel():
        async with factory() as session:
            user = await session.get(User, student_id)
            try:
                await routes.cancel_assignment(
                    "course-a",
                    student_id,
                    assignment_id,
                    routes.AssignmentCancel(
                        expected_version=1, reason="Change the coaching focus today"
                    ),
                    user,
                    session,
                )
                return "cancelled"
            except fastapi.HTTPException as error:
                assert error.status_code == 409
                return "conflict"

    results = await asyncio.gather(finish(), cancel())
    assert results.count("conflict") == 1
    async with factory() as session:
        row = await session.get(CoachingAssignment, assignment_id)
        count = (
            await session.execute(
                sa.select(sa.func.count()).select_from(PracticeDrillCompletion)
            )
        ).scalar_one()
        assert count == (1 if row.status == "completed" else 0)


@pytest.mark.asyncio
async def test_student_preflight_requires_ownership_and_current_enrollment(db):
    _, factory, student_id = db
    assignment_id = await seed(factory, student_id)
    async with factory() as session:
        owner = await session.get(User, student_id)
        response = await routes.own_assignment(
            assignment_id, fastapi.Response(), owner, session
        )
        assert response["status"] == "assigned"
        with pytest.raises(fastapi.HTTPException) as error:
            await routes.own_assignment(
                assignment_id, fastapi.Response(), User(id=student_id + 500), session
            )
        assert error.value.status_code == 404
        owner.enrolled_course_ids = []
        await session.commit()
        with pytest.raises(fastapi.HTTPException) as error:
            await routes.own_assignment(
                assignment_id, fastapi.Response(), owner, session
            )
        assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_past_due_creation_rejected_but_late_completion_allowed(db, monkeypatch):
    _, factory, student_id = db
    monkeypatch.setattr(routes, "authorize_staff", AsyncMock())
    async with factory() as session:
        user = await session.get(User, student_id)
        with pytest.raises(fastapi.HTTPException) as error:
            await routes.create_assignment(
                "course-a",
                student_id,
                routes.AssignmentCreate(
                    request_key=uuid4(),
                    module="warm_up",
                    reason="Practise your opening answer",
                    due_date=datetime.date(2020, 1, 1),
                ),
                fastapi.Response(),
                user,
                session,
            )
        assert error.value.status_code == 422
        await session.rollback()
    assignment_id = await seed(factory, student_id)
    async with factory() as session:
        row = await session.get(CoachingAssignment, assignment_id)
        row.due_date = datetime.date(2020, 1, 1)
        await session.commit()
        _, created = await record_practice_drill_completion(
            session, user_id=student_id, payload=payload(assignment_id)
        )
        assert created
        with pytest.raises(fastapi.HTTPException) as error:
            await record_practice_drill_completion(
                session,
                user_id=student_id,
                payload=payload(assignment_id).model_copy(
                    update={"evidence_units": 1000}
                ),
            )
        assert error.value.status_code == 409
