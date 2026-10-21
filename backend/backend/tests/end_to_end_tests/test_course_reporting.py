"""Real PostgreSQL scope parity across roster search, summary and CSV export."""

import csv
import datetime
import io

import fastapi
import httpx
import pytest
import sqlalchemy as sa

from .test_fixed_question_rounds import db, pytestmark  # noqa: F401
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.features import feature_gate
from src.api.dependencies.session import get_async_session
from src.api.routes import course_workspace
from src.models.db.user import User
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.interview import Interview
from src.models.db.practice_governance import (
    PracticeConsent,
    PracticeDashboardAuditEvent,
)
from src.services import sampark_enrollment_sync


@pytest.fixture
async def reporting(db, monkeypatch):
    _, factory, actor_id = db
    monkeypatch.setattr(sampark_enrollment_sync, "enabled", lambda: True)
    now = datetime.datetime.now(datetime.timezone.utc)
    ids = {}
    async with factory() as session:
        actor = await session.get(User, actor_id)
        actor.feature_permissions = {"course_workspace": ["read", "export"]}
        for name, college, course in [
            ("Program A", "College A", "course-a"),
            ("Program B", "College B", "course-b"),
        ]:
            team = Team(name=name, status="active")
            session.add(team)
            await session.flush()
            session.add(
                TeamMember(
                    team_id=team.id,
                    user_id=actor_id,
                    access_role="facilitator",
                    course_ids=[course],
                    colleges=[college],
                    status="active",
                )
            )
        specs = [
            ("alice", "=Alice", "College A", ["course-a"], True, False),
            ("zero", "Zero", "College A", ["course-a"], True, False),
            ("literal", "100%_Ready", "College A", ["course-a"], True, False),
            ("wrong_college", "Hidden College", "College B", ["course-a"], True, False),
            ("wrong_course", "Hidden Course", "College A", ["course-b"], True, False),
            ("no_consent", "Hidden Consent", "College A", ["course-a"], False, False),
            ("stale", "Hidden Stale", "College A", ["course-a"], True, True),
        ]
        for key, name, college, courses, consent, stale in specs:
            student = User(
                name=name,
                email=f"{key}@example.test",
                password_hash="test-only",
                university=college,
                enrolled_course_ids=courses,
                enrollment_source="sampark",
                enrollment_observed_at=(
                    now - datetime.timedelta(days=30) if stale else now
                ),
            )
            session.add(student)
            await session.flush()
            ids[key] = student.id
            session.add(
                PracticeConsent(
                    user_id=student.id,
                    purpose="facilitator_coaching",
                    granted=consent,
                    policy_version="test",
                    granted_at=now,
                    expires_at=now + datetime.timedelta(days=7),
                )
            )
        for course, mode, status in [
            ("course-a", "practice", "completed"),
            ("course-a", "practice", "completed"),
            ("course-b", "practice", "completed"),
            ("course-a", "assessment", "completed"),
            (None, "practice", "completed"),
            ("course-a", "practice", "in_progress"),
        ]:
            session.add(
                Interview(
                    user_id=ids["alice"],
                    course_id=course,
                    interview_mode=mode,
                    status=status,
                    track="Communication",
                )
            )
        await session.commit()
    app = fastapi.FastAPI()
    app.include_router(
        course_workspace.router,
        dependencies=[fastapi.Depends(feature_gate("course_workspace"))],
    )

    async def session_dep():
        async with factory() as session:
            try:
                yield session
            finally:
                await session.rollback()

    async def current(session=fastapi.Depends(get_async_session)):
        return await session.get(User, actor_id)

    app.dependency_overrides[get_async_session] = session_dep
    app.dependency_overrides[get_current_user] = current
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, factory, actor_id, ids


@pytest.mark.asyncio
async def test_search_summary_pagination_and_export_share_authorized_rows(reporting):
    client, factory, actor_id, ids = reporting
    base = "/v2/course-workspace/courses/course-a/students"
    response = await client.get(base, params={"page": 2, "limit": 1})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 3 and len(data["items"]) == 1
    assert data["summary"] == {
        "practised_students": 1,
        "not_started_students": 2,
        "completed_practice_rounds": 2,
    }
    assert response.headers["cache-control"] == "no-store"
    assert data["can_export"]
    practised = (await client.get(base, params={"activity": "practised"})).json()
    assert [row["id"] for row in practised["items"]] == [ids["alice"]]
    assert practised["summary"]["completed_practice_rounds"] == 2
    literal = (await client.get(base, params={"q": "%_"})).json()
    assert [row["id"] for row in literal["items"]] == [ids["literal"]]
    no_match = (await client.get(base, params={"q": "Hidden"})).json()
    assert (
        no_match["total"] == 0 and no_match["summary"]["completed_practice_rounds"] == 0
    )
    response = await client.post(
        base + "/export", json={"reason": "Weekly shared practice review"}
    )
    assert response.status_code == 200, response.text
    rows = list(csv.reader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert len(rows) == 4 and len(rows[0]) == 4
    assert {int(row[0]) for row in rows[1:]} == {
        ids["alice"],
        ids["zero"],
        ids["literal"],
    }
    assert rows[1][1] == "'=Alice"
    assert "Hidden" not in response.text
    assert response.headers["cache-control"] == "no-store"
    async with factory() as session:
        event = (
            await session.execute(
                sa.select(PracticeDashboardAuditEvent).where(
                    PracticeDashboardAuditEvent.action == "course_roster_exported"
                )
            )
        ).scalar_one()
        assert event.actor_id == actor_id and event.event_metadata["rows"] == 3


@pytest.mark.asyncio
async def test_export_requires_explicit_operation_and_fresh_scope(reporting):
    client, factory, actor_id, ids = reporting
    base = "/v2/course-workspace/courses/course-a/students"
    for policy in [None, {"course_workspace": ["read"]}]:
        async with factory() as session:
            (await session.get(User, actor_id)).feature_permissions = policy
            await session.commit()
        assert (await client.get(base)).json()["can_export"] is False
        assert (
            await client.post(
                base + "/export", json={"reason": "Weekly practice review"}
            )
        ).status_code == 403
    async with factory() as session:
        (await session.get(User, actor_id)).feature_permissions = {
            "course_workspace": ["read", "export"]
        }
        await session.execute(
            sa.update(PracticeConsent)
            .where(PracticeConsent.user_id == ids["alice"])
            .values(withdrawn_at=datetime.datetime.now(datetime.timezone.utc))
        )
        await session.commit()
    assert (await client.get(base)).json()["total"] == 2
    response = await client.post(
        base + "/export", json={"q": "Alice", "reason": "Weekly practice review"}
    )
    assert (
        response.status_code == 200
        and len(list(csv.reader(io.StringIO(response.text.lstrip("\ufeff"))))) == 1
    )
    async with factory() as session:
        await session.execute(
            sa.update(TeamMember)
            .where(
                TeamMember.user_id == actor_id,
                TeamMember.course_ids.contains(["course-a"]),
            )
            .values(status="inactive")
        )
        await session.commit()
    assert (await client.get(base)).status_code == 403
    assert (
        await client.post(base + "/export", json={"reason": "Weekly practice review"})
    ).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{"q": "x" * 101}, {"activity": "everything"}])
async def test_invalid_roster_filters_rejected(reporting, params):
    client, *_ = reporting
    assert (
        await client.get(
            "/v2/course-workspace/courses/course-a/students", params=params
        )
    ).status_code == 422
