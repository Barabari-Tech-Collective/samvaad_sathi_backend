import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from src.services import sampark_enrollment_sync as sync


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(sync.settings, "SAMPARK_ENROLLMENT_SYNC_ENABLED", True)
    monkeypatch.setattr(sync.settings, "SAMPARK_ENROLLMENT_MAX_AGE_SECONDS", 900)


def test_freshness_rejects_old_unknown_and_future_sources(configured):
    now = datetime.datetime.now(datetime.timezone.utc)
    user = SimpleNamespace(enrollment_source="sampark", enrollment_observed_at=now)
    assert sync.fresh(user, now)
    for source, when in [
        ("manual", now),
        ("sampark", now - datetime.timedelta(minutes=16)),
        ("sampark", now + datetime.timedelta(minutes=6)),
        ("sampark", None),
    ]:
        user.enrollment_source = source
        user.enrollment_observed_at = when
        assert not sync.fresh(user, now)


@pytest.mark.asyncio
async def test_authoritative_empty_snapshot_revokes_courses(configured):
    now = datetime.datetime.now(datetime.timezone.utc)
    student = SimpleNamespace(
        email="student@example.test",
        sampark_student_unique_id="central-1",
        enrollment_observed_at=now - datetime.timedelta(minutes=5),
        enrolled_course_ids=["1"],
        enrollment_source="sampark",
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [student]
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    page = sync.Page(
        source="sampark",
        observedAt=now,
        items=[
            sync.Snapshot(
                studentId="1",
                studentUniqueId="central-1",
                email=student.email,
                courseIds=[],
            )
        ],
    )
    assert await sync.apply_page(session, page) == 1
    assert student.enrolled_course_ids == []


@pytest.mark.asyncio
async def test_older_snapshot_does_not_restore_revoked_access(configured):
    now = datetime.datetime.now(datetime.timezone.utc)
    student = SimpleNamespace(
        email="student@example.test",
        sampark_student_unique_id="central-1",
        enrollment_observed_at=now,
        enrolled_course_ids=[],
        enrollment_source="sampark",
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [student]
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    page = sync.Page(
        source="sampark",
        observedAt=now - datetime.timedelta(minutes=1),
        items=[
            sync.Snapshot(
                studentId="1",
                studentUniqueId="central-1",
                email=student.email,
                courseIds=["1"],
            )
        ],
    )
    assert await sync.apply_page(session, page) == 0
    assert student.enrolled_course_ids == []


@pytest.mark.asyncio
async def test_ambiguous_source_identity_is_rejected_before_writes(configured):
    page = sync.Page(
        source="sampark",
        observedAt=datetime.datetime.now(datetime.timezone.utc),
        items=[
            sync.Snapshot(
                studentId=str(i),
                studentUniqueId="same",
                email=f"{i}@example.test",
                courseIds=[],
            )
            for i in (1, 2)
        ],
    )
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(ValueError, match="Ambiguous"):
        await sync.apply_page(session, page)
    session.execute.assert_not_awaited()
