"""Receipt payload identity and enrollment/assignment ordering on PostgreSQL."""

import asyncio
import datetime
from types import SimpleNamespace
from uuid import uuid4

import fastapi
import pytest
import sqlalchemy as sa

from .test_fixed_question_rounds import db, pytestmark  # noqa: F401
from .test_coaching_assignments import seed, payload
from src.api.routes import coaching_assignments as routes
from src.models.db.coaching_assignment import CoachingAssignment
from src.models.db.practice_drill_completion import PracticeDrillCompletion
from src.models.db.practice_governance import (
    PracticeDashboardAuditEvent,
    PracticeConsent,
)
from src.models.db.user import User
from src.services.practice_drill_completion import (
    record_practice_drill_completion as record,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("module", "think_fast"),
        ("level", "advanced"),
        ("evidence_kind", "text"),
        ("evidence_units", 1024),
    ],
)
async def test_independent_receipt_cannot_acknowledge_changed_payload(db, field, value):
    _, factory, user_id = db
    original = payload(None)
    async with factory() as session:
        first, created = await record(session, user_id=user_id, payload=original)
        receipt_id = first.id
        assert created
        again, created = await record(session, user_id=user_id, payload=original)
        assert not created and again.id == receipt_id
        with pytest.raises(fastapi.HTTPException) as error:
            await record(
                session,
                user_id=user_id,
                payload=original.model_copy(update={field: value}),
            )
        assert error.value.status_code == 409
        await session.rollback()
        row = await session.get(PracticeDrillCompletion, receipt_id)
        assert (
            row.module == original.module
            and row.level == original.level
            and row.evidence_units == original.evidence_units
            and row.evidence_kind == original.evidence_kind
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("different", [False, True])
async def test_simultaneous_independent_receipts_have_one_winner(db, different):
    _, factory, user_id = db

    async def finish(level):
        async with factory() as session:
            try:
                return (
                    await record(
                        session,
                        user_id=user_id,
                        payload=payload(None).model_copy(update={"level": level}),
                    )
                )[1]
            except fastapi.HTTPException as error:
                assert error.status_code == 409
                return "conflict"

    results = await asyncio.gather(
        finish("beginner"), finish("advanced" if different else "beginner")
    )
    assert results.count(True) == 1
    assert results.count("conflict") == int(different)
    async with factory() as session:
        assert (
            await session.execute(
                sa.select(sa.func.count()).select_from(PracticeDrillCompletion)
            )
        ).scalar_one() == 1


@pytest.mark.asyncio
async def test_assigned_receipt_cannot_be_replayed_as_independent_or_other_assignment(
    db,
):
    _, factory, user_id = db
    assignment_id = await seed(factory, user_id)
    other_id = await seed(factory, user_id)
    async with factory() as session:
        first, created = await record(
            session, user_id=user_id, payload=payload(assignment_id)
        )
        assert created
        receipt_id = first.id
        for target in (None, other_id):
            with pytest.raises(fastapi.HTTPException) as error:
                await record(session, user_id=user_id, payload=payload(target))
            assert error.value.status_code == 409
            await session.rollback()
        row = await session.get(CoachingAssignment, other_id)
        assert row.status == "assigned" and row.completion_id is None
        retry, created = await record(
            session, user_id=user_id, payload=payload(assignment_id)
        )
        assert not created and retry.id == receipt_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("level", "advanced"),
        ("evidence_units", 1024),
        ("client_completion_id", "different:receipt"),
    ],
)
async def test_assigned_receipt_retries_require_exact_original_evidence(
    db, field, value
):
    _, factory, user_id = db
    assignment_id = await seed(factory, user_id)
    async with factory() as session:
        await record(session, user_id=user_id, payload=payload(assignment_id))
        with pytest.raises(fastapi.HTTPException) as error:
            await record(
                session,
                user_id=user_id,
                payload=payload(assignment_id).model_copy(update={field: value}),
            )
        assert error.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_receipt", ["missing", "owner", "module", "kind"])
async def test_inconsistent_legacy_assignment_receipt_fails_closed(db, bad_receipt):
    _, factory, user_id = db
    assignment_id = await seed(factory, user_id)
    async with factory() as session:
        row = await session.get(CoachingAssignment, assignment_id)
        row.status = "completed"
        if bad_receipt != "missing":
            owner = User(email="other@example.test", name="Other", password_hash="test")
            session.add(owner)
            await session.flush()
            receipt = PracticeDrillCompletion(
                user_id=owner.id if bad_receipt == "owner" else user_id,
                module="think_fast" if bad_receipt == "module" else "warm_up",
                level="beginner",
                client_completion_id="warmup:receipt001",
                evidence_kind="text" if bad_receipt == "kind" else "audio",
                evidence_units=512,
            )
            session.add(receipt)
            await session.flush()
            row.completion_id = receipt.id
        await session.commit()
        with pytest.raises(fastapi.HTTPException) as error:
            await record(session, user_id=user_id, payload=payload(assignment_id))
        assert error.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("restore", [False, True])
async def test_completion_waits_for_enrollment_writer_and_uses_committed_state(
    db, restore
):
    _, factory, user_id = db
    assignment_id = await seed(factory, user_id)
    if restore:
        async with factory() as session:
            user = await session.get(User, user_id)
            user.enrolled_course_ids = []
            await session.commit()

    async def finish():
        async with factory() as session:
            # Cache old rows before the writer commits; locked reads must refresh.
            await session.get(User, user_id)
            await session.get(CoachingAssignment, assignment_id)
            try:
                return (
                    await record(
                        session, user_id=user_id, payload=payload(assignment_id)
                    )
                )[1]
            except fastapi.HTTPException as error:
                return error.status_code

    async with factory() as writer:
        user = await writer.get(User, user_id, with_for_update=True)
        user.enrolled_course_ids = ["course-a"] if restore else []
        await writer.flush()
        task = asyncio.create_task(finish())
        await asyncio.sleep(0.05)
        assert not task.done()
        await writer.commit()
    assert await asyncio.wait_for(task, 3) == (True if restore else 403)
    async with factory() as session:
        row = await session.get(CoachingAssignment, assignment_id)
        assert row.status == ("completed" if restore else "assigned")
        count = (
            await session.execute(
                sa.select(sa.func.count()).select_from(PracticeDashboardAuditEvent)
            )
        ).scalar_one()
        assert count == int(restore)


@pytest.mark.asyncio
async def test_completed_receipt_remains_idempotent_after_enrollment_revocation(db):
    _, factory, user_id = db
    assignment_id = await seed(factory, user_id)
    async with factory() as session:
        first, _ = await record(
            session, user_id=user_id, payload=payload(assignment_id)
        )
        receipt_id = first.id
        user = await session.get(User, user_id)
        user.enrolled_course_ids = []
        await session.commit()
        retry, created = await record(
            session, user_id=user_id, payload=payload(assignment_id)
        )
        assert not created and retry.id == receipt_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot", ["stale", "future", "wrong_source", "missing_timestamp"]
)
async def test_assigned_completion_rejects_unverified_enrollment_without_writes(
    db, monkeypatch, snapshot
):
    from src.services import sampark_enrollment_sync as sync

    _, factory, user_id = db
    assignment_id = await seed(factory, user_id)
    monkeypatch.setattr(sync, "enabled", lambda: True)
    monkeypatch.setattr(sync, "max_age", lambda: 3600)
    async with factory() as session:
        user = await session.get(User, user_id)
        user.enrollment_source = "other" if snapshot == "wrong_source" else "sampark"
        user.enrollment_observed_at = datetime.datetime.now(
            datetime.timezone.utc
        ) + datetime.timedelta(
            hours=2 if snapshot == "future" else -2 if snapshot == "stale" else 0
        )
        if snapshot == "missing_timestamp":
            user.enrollment_observed_at = None
        await session.commit()
        with pytest.raises(fastapi.HTTPException) as error:
            await record(session, user_id=user_id, payload=payload(assignment_id))
        assert error.value.status_code == 403
        await session.rollback()
        assert (
            await session.get(CoachingAssignment, assignment_id)
        ).status == "assigned"
        assert (
            await session.execute(
                sa.select(sa.func.count()).select_from(PracticeDrillCompletion)
            )
        ).scalar_one() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "cancel"])
async def test_staff_mutation_rechecks_real_enrollment_after_waiting_for_student_lock(
    db, operation
):
    _, factory, user_id = db
    assignment_id = await seed(factory, user_id)
    async with factory() as session:
        session.add(
            PracticeConsent(
                user_id=user_id,
                purpose="facilitator_coaching",
                granted=True,
                policy_version="1",
            )
        )
        await session.commit()
    # Explicit admin authority; target is still a student and must be enrolled/consenting.
    actor = SimpleNamespace(id=user_id, is_admin=True)

    async def mutate():
        async with factory() as session:
            try:
                if operation == "create":
                    return await routes.create_assignment(
                        "course-a",
                        user_id,
                        routes.AssignmentCreate(
                            request_key=uuid4(),
                            module="warm_up",
                            reason="Practise clear opening answers",
                        ),
                        fastapi.Response(),
                        actor,
                        session,
                    )
                return await routes.cancel_assignment(
                    "course-a",
                    user_id,
                    assignment_id,
                    routes.AssignmentCancel(
                        expected_version=1, reason="Change the coaching plan today"
                    ),
                    actor,
                    session,
                )
            except fastapi.HTTPException as error:
                return error.status_code

    async with factory() as writer:
        user = await writer.get(User, user_id, with_for_update=True)
        user.enrolled_course_ids = []
        await writer.flush()
        task = asyncio.create_task(mutate())
        await asyncio.sleep(0.05)
        assert not task.done()
        await writer.commit()
    assert await asyncio.wait_for(task, 3) == 404
    async with factory() as session:
        assert (
            await session.get(CoachingAssignment, assignment_id)
        ).status == "assigned"
        assert (
            await session.execute(
                sa.select(sa.func.count()).select_from(CoachingAssignment)
            )
        ).scalar_one() == 1
        assert (
            await session.execute(
                sa.select(sa.func.count()).select_from(PracticeDashboardAuditEvent)
            )
        ).scalar_one() == 0


@pytest.mark.asyncio
async def test_completion_winning_user_lock_commits_before_later_revocation(
    db, monkeypatch
):
    from src.services import coaching_assignments as service

    _, factory, user_id = db
    assignment_id = await seed(factory, user_id)
    claimed = asyncio.Event()
    proceed = asyncio.Event()
    original = service.lock_student

    async def paused_lock(session, target):
        row = await original(session, target)
        claimed.set()
        await proceed.wait()
        return row

    monkeypatch.setattr(service, "lock_student", paused_lock)

    async def finish():
        async with factory() as session:
            return (
                await record(session, user_id=user_id, payload=payload(assignment_id))
            )[1]

    async def revoke():
        async with factory() as session:
            await session.execute(
                sa.update(User).where(User.id == user_id).values(enrolled_course_ids=[])
            )
            await session.commit()

    completing = asyncio.create_task(finish())
    await asyncio.wait_for(claimed.wait(), 3)
    revoking = asyncio.create_task(revoke())
    await asyncio.sleep(0.05)
    assert not revoking.done()
    proceed.set()
    assert await asyncio.wait_for(completing, 3)
    await asyncio.wait_for(revoking, 3)
    async with factory() as session:
        assert (await session.get(User, user_id)).enrolled_course_ids == []
        assert (
            await session.get(CoachingAssignment, assignment_id)
        ).status == "completed"


@pytest.mark.asyncio
async def test_independent_and_assigned_requests_competing_for_receipt_cannot_both_succeed(
    db,
):
    _, factory, user_id = db
    assignment_id = await seed(factory, user_id)

    async def finish(target):
        async with factory() as session:
            try:
                _, created = await record(
                    session, user_id=user_id, payload=payload(target)
                )
                assert created
                return target
            except fastapi.HTTPException as error:
                assert error.status_code == 409
                return "conflict"

    results = await asyncio.gather(finish(None), finish(assignment_id))
    assert results.count("conflict") == 1
    async with factory() as session:
        assert (
            await session.execute(
                sa.select(sa.func.count()).select_from(PracticeDrillCompletion)
            )
        ).scalar_one() == 1
        row = await session.get(CoachingAssignment, assignment_id)
        assert row.status == ("completed" if assignment_id in results else "assigned")


@pytest.mark.asyncio
async def test_receipt_keys_are_scoped_to_student_identity(db):
    _, factory, user_id = db
    async with factory() as session:
        other = User(email="second@example.test", name="Second", password_hash="test")
        session.add(other)
        await session.commit()
        other_id = other.id
        first, _ = await record(session, user_id=user_id, payload=payload(None))
        first_id = first.id
        second, created = await record(session, user_id=other_id, payload=payload(None))
        assert created and first_id != second.id and second.user_id == other_id


@pytest.mark.asyncio
async def test_assignment_user_lock_allows_independent_receipt_fk_insert_without_deadlock(
    db, monkeypatch
):
    from src.services import coaching_assignments as service

    _, factory, user_id = db
    assignment_id = await seed(factory, user_id)
    claimed, proceed = asyncio.Event(), asyncio.Event()
    original = service.lock_student

    async def paused_lock(session, target):
        student = await original(session, target)
        claimed.set()
        await proceed.wait()
        return student

    monkeypatch.setattr(service, "lock_student", paused_lock)

    async def assigned():
        async with factory() as session:
            try:
                await record(session, user_id=user_id, payload=payload(assignment_id))
            except fastapi.HTTPException as error:
                return error.status_code

    async def independent():
        async with factory() as session:
            return (await record(session, user_id=user_id, payload=payload(None)))[1]

    task = asyncio.create_task(assigned())
    try:
        await asyncio.wait_for(claimed.wait(), 3)
        assert await asyncio.wait_for(independent(), 3)
    finally:
        proceed.set()
    assert await asyncio.wait_for(task, 3) == 409
    async with factory() as session:
        assert (
            await session.get(CoachingAssignment, assignment_id)
        ).status == "assigned"
