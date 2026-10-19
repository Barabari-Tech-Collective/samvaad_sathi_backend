"""Reuse the explicitly isolated PostgreSQL fixture; HTTP source is mocked."""

import datetime
import httpx
import pytest
import sqlalchemy as sa
from src.models.db.user import User
from src.services import sampark_enrollment_sync as sync
from .test_fixed_question_rounds import db  # noqa: F401


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(sync.settings, "SAMPARK_ENROLLMENT_SYNC_ENABLED", True)
    monkeypatch.setattr(sync.settings, "SAMPARK_ENROLLMENT_MAX_AGE_SECONDS", 900)
    monkeypatch.setattr(
        sync.settings,
        "SAMPARK_ENROLLMENT_SYNC_URL",
        "https://sampark.example.test/snapshot",
    )
    monkeypatch.setattr(sync.settings, "SAMPARK_ENROLLMENT_SYNC_KEY", "test-only-key")


def response(items, next_cursor=None):
    return {
        "source": "sampark",
        "observedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "items": items,
        "nextCursor": next_cursor,
    }


@pytest.mark.asyncio
async def test_full_pull_updates_revokes_and_reconciles_missing_users(db, configured):
    _, factory, student_id = db
    async with factory() as session:
        student = await session.get(User, student_id)
        email = student.email
        session.add(
            User(
                email="removed@example.test",
                name="Removed",
                password_hash="test",
                sampark_student_unique_id="removed",
                enrolled_course_ids=["old"],
            )
        )
        await session.commit()

    def source(request):
        assert request.headers["X-Sampark-Enrollment-Key"] == "test-only-key"
        return httpx.Response(
            200,
            json=response(
                [
                    {
                        "studentId": "1",
                        "studentUniqueId": "stable-1",
                        "email": email,
                        "courseIds": ["course-1"],
                    }
                ]
            ),
        )

    async with factory() as session:
        result = await sync.synchronize(session, transport=httpx.MockTransport(source))
        assert result["status"] == "complete"
    async with factory() as session:
        student = await session.get(User, student_id)
        assert student.enrolled_course_ids == ["course-1"] and sync.fresh(student)
        removed = (
            await session.execute(
                sa.select(User).where(User.email == "removed@example.test")
            )
        ).scalar_one()
        assert removed.enrolled_course_ids == []

    def revoked(_request):
        return httpx.Response(
            200,
            json=response(
                [
                    {
                        "studentId": "1",
                        "studentUniqueId": "stable-1",
                        "email": email,
                        "courseIds": [],
                    }
                ]
            ),
        )

    async with factory() as session:
        await sync.synchronize(session, transport=httpx.MockTransport(revoked))
    async with factory() as session:
        assert (await session.get(User, student_id)).enrolled_course_ids == []


@pytest.mark.asyncio
async def test_partial_pull_rolls_back_without_mass_revocation(db, configured):
    _, factory, student_id = db
    async with factory() as session:
        student = await session.get(User, student_id)
        email = student.email
        student.enrolled_course_ids = ["original"]
        await session.commit()

    def source(request):
        if request.url.params["after"] == "0":
            return httpx.Response(
                200,
                json=response(
                    [
                        {
                            "studentId": "1",
                            "studentUniqueId": "stable-1",
                            "email": email,
                            "courseIds": ["uncommitted"],
                        }
                    ],
                    "1",
                ),
            )
        return httpx.Response(503, json={"detail": "unavailable"})

    async with factory() as session:
        with pytest.raises(httpx.HTTPStatusError):
            await sync.synchronize(session, transport=httpx.MockTransport(source))
        await session.rollback()
    async with factory() as session:
        student = await session.get(User, student_id)
        assert (
            student.enrolled_course_ids == ["original"]
            and student.sampark_student_unique_id is None
        )


@pytest.mark.asyncio
async def test_stale_enrollment_denies_new_course_attempt(db, configured):
    from src.repository.crud.interview import InterviewCRUDRepository
    import fastapi

    _, factory, student_id = db
    async with factory() as session:
        student = await session.get(User, student_id)
        student.enrolled_course_ids = ["course-1"]
        student.enrollment_source = "sampark"
        student.enrollment_observed_at = datetime.datetime.now(
            datetime.timezone.utc
        ) - datetime.timedelta(hours=1)
        await session.commit()
        with pytest.raises(fastapi.HTTPException) as error:
            await InterviewCRUDRepository(session).create_interview(
                user_id=student_id, track="Backend", course_id="course-1"
            )
        assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_enrollment_migration_preserves_old_courses_without_claiming_freshness(
    db,
):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine, factory, user_id = db
    async with factory() as session:
        student = await session.get(User, user_id)
        student.enrolled_course_ids = ["existing"]
        await session.commit()
    path = (
        Path(__file__).parents[2]
        / "src/repository/migrations/versions/2026_10_19_0400-b0d1e2f3a4b5_sampark_enrollment_freshness.py"
    )
    spec = importlib.util.spec_from_file_location("freshness_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with engine.begin() as conn:
        for column in (
            "enrollment_source",
            "enrollment_observed_at",
            "sampark_student_unique_id",
        ):
            await conn.execute(
                sa.text(f'ALTER TABLE "user" DROP COLUMN {column} CASCADE')
            )

        def upgrade(sync_connection):
            with Operations.context(MigrationContext.configure(sync_connection)):
                migration.upgrade()

        await conn.run_sync(upgrade)
    async with factory() as session:
        student = await session.get(User, user_id)
        assert student.enrolled_course_ids == ["existing"]
        assert (
            student.enrollment_observed_at is None and student.enrollment_source is None
        )


@pytest.mark.asyncio
async def test_assignment_conflict_preserves_last_committed_grant(db):
    from src.models.db.team import Team
    from src.models.db.team_member import TeamMember
    from src.api.routes.course_workspace import (
        assignment_version,
        assign_member_courses,
        CourseAssignment,
    )
    from types import SimpleNamespace
    import fastapi

    _, factory, user_id = db
    async with factory() as session:
        team = Team(name="Test course team", status="active")
        session.add(team)
        await session.flush()
        member = TeamMember(
            team_id=team.id,
            user_id=user_id,
            access_role="facilitator",
            status="active",
            course_ids=["1"],
        )
        session.add(member)
        await session.commit()
        member_id = member.id
        version = assignment_version(member)
    admin = SimpleNamespace(
        id=user_id, is_admin=True, is_super_admin=False, email="admin@example.test"
    )
    async with factory() as session:
        accepted = await assign_member_courses(
            member_id,
            CourseAssignment(
                course_ids=["2"],
                reason="Changed teaching assignment",
                expected_version=version,
            ),
            admin,
            session,
        )
        assert accepted["course_ids"] == ["2"]
    async with factory() as session:
        with pytest.raises(fastapi.HTTPException) as error:
            await assign_member_courses(
                member_id,
                CourseAssignment(
                    course_ids=["3"],
                    reason="Stale concurrent assignment",
                    expected_version=version,
                ),
                admin,
                session,
            )
        assert error.value.status_code == 409
        await session.rollback()
    async with factory() as session:
        assert (await session.get(TeamMember, member_id)).course_ids == ["2"]
