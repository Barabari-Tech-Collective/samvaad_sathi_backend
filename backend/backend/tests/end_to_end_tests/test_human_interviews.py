"""Real PostgreSQL appointment scheduling, scope isolation and lifecycle."""

import asyncio
import datetime as dt
from uuid import uuid4
import pytest
import sqlalchemy as sa
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401
from .test_course_reporting import reporting  # noqa: F401
from src.api.routes import human_interviews as routes
from src.api.dependencies.auth import get_current_user
from src.models.db.user import User
from src.models.db.team_member import TeamMember
from src.models.db.human_interview import HumanInterview
from src.models.db.interview import Interview

BASE = "/v2/human-interviews/courses/course-a"


@pytest.fixture
async def appointments(reporting):
    client, factory, actor_id, ids = reporting
    app = client._transport.app
    app.include_router(routes.router)
    async with factory() as session:
        (await session.get(User, actor_id)).feature_permissions = {
            "human_interviews": ["read", "create", "update"]
        }
        await session.commit()
    yield client, factory, actor_id, ids


def body(student, **changes):
    start = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)
    return {
        "request_key": str(uuid4()),
        "student_id": student,
        "title": "Human engineering interview",
        "meet_url": "https://meet.google.com/abc-defg-hij",
        "starts_at": start.isoformat(),
        "ends_at": (start + dt.timedelta(minutes=30)).isoformat(),
        **changes,
    }


async def as_student(client, factory, student):
    async def current():
        async with factory() as session:
            return await session.get(User, student)

    client._transport.app.dependency_overrides[get_current_user] = current


@pytest.mark.asyncio
async def test_schedule_retry_private_join_and_no_ai_attempt(appointments):
    client, factory, actor, ids = appointments
    request = body(ids["alice"])
    response = await client.post(BASE, json=request)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["format"] == "human_google_meet" and "meet_url" not in row
    assert (await client.post(BASE, json=request)).status_code == 200
    assert (
        await client.post(BASE, json={**request, "title": "Different interview"})
    ).status_code == 409
    assert (
        await client.post(BASE, json=body(ids["zero"]))
    ).status_code == 409  # host overlap
    link = await client.get(f"{BASE}/{row['id']}/join")
    assert link.status_code == 200 and link.headers["cache-control"] == "no-store"
    await as_student(client, factory, ids["zero"])
    assert (
        await client.get(f"/v2/human-interviews/me/{row['id']}/join")
    ).status_code == 404
    await as_student(client, factory, ids["alice"])
    mine = await client.get("/v2/human-interviews/me")
    assert mine.status_code == 200 and mine.json()["total"] == 1
    assert "meet_url" not in mine.text
    assert (
        await client.get(f"/v2/human-interviews/me/{row['id']}/join")
    ).status_code == 200
    async with factory() as session:
        assert (
            await session.execute(sa.select(sa.func.count()).select_from(Interview))
        ).scalar_one() == 6  # unchanged reporting fixture
        (await session.get(User, ids["alice"])).enrolled_course_ids = []
        await session.commit()
    assert (await client.get("/v2/human-interviews/me")).json()["total"] == 0
    assert (
        await client.get(f"/v2/human-interviews/me/{row['id']}/join")
    ).status_code == 404


@pytest.mark.asyncio
async def test_scope_freshness_explicit_opt_in_and_join_window(appointments):
    client, factory, actor, ids = appointments
    for key in ["wrong_course", "wrong_college", "stale"]:
        assert (await client.post(BASE, json=body(ids[key]))).status_code == 404
    request = body(
        ids["alice"],
        starts_at=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat(),
        ends_at=(
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1, minutes=30)
        ).isoformat(),
    )
    response = await client.post(BASE, json=request)
    assert response.status_code == 201, response.text
    row = response.json()
    assert not row["can_join"]
    assert (await client.get(f"{BASE}/{row['id']}/join")).status_code == 409
    assert (
        await client.get("/v2/human-interviews/courses/course-c")
    ).status_code == 403
    for policy in [None, {}, {"human_interviews": ["read"]}]:
        async with factory() as session:
            (await session.get(User, actor)).feature_permissions = policy
            await session.commit()
        assert (await client.post(BASE, json=body(ids["zero"]))).status_code == 403


@pytest.mark.asyncio
async def test_manual_close_versions_and_no_early_no_show(appointments):
    client, factory, actor, ids = appointments
    created = await client.post(BASE, json=body(ids["alice"]))
    row = created.json()
    url = f"{BASE}/{row['id']}/close"
    payload = {
        "expected_version": 1,
        "reason": "Host reviewed the meeting situation",
        "status": "completed",
    }
    assert (await client.post(url, json=payload)).status_code == 409
    assert (
        await client.post(url, json={**payload, "status": "no_show"})
    ).status_code == 409
    results = await asyncio.gather(
        *[
            client.post(url, json={**payload, "status": state})
            for state in ["cancelled", "technical_issue"]
        ]
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    assert (await client.get(f"{BASE}/{row['id']}/join")).status_code == 409
    await as_student(client, factory, ids["alice"])
    data = (await client.get("/v2/human-interviews/me")).json()["items"][0]
    assert data["version"] == 2 and data["outcome_source"] == "host_reported"
    assert data["attendance"] == "not_verified" and not data["can_join"]


@pytest.mark.asyncio
async def test_simultaneous_schedule_cannot_double_book(appointments):
    client, factory, actor, ids = appointments
    requests = [body(ids[key]) for key in ["alice", "zero"]]
    responses = await asyncio.gather(
        *[client.post(BASE, json=value) for value in requests]
    )
    assert sorted(r.status_code for r in responses) == [201, 409]


@pytest.mark.asyncio
async def test_host_revocation_blocks_student_link_and_stale_source(appointments):
    client, factory, actor, ids = appointments
    row = (await client.post(BASE, json=body(ids["alice"]))).json()
    async with factory() as session:
        (await session.get(User, actor)).feature_permissions = {}
        await session.commit()
    await as_student(client, factory, ids["alice"])
    assert (
        await client.get(f"/v2/human-interviews/me/{row['id']}/join")
    ).status_code == 409


@pytest.mark.asyncio
async def test_revoked_enrollment_blocks_join_but_host_can_cancel(appointments):
    client, factory, actor, ids = appointments
    row = (await client.post(BASE, json=body(ids["alice"]))).json()
    async with factory() as session:
        (await session.get(User, ids["alice"])).enrolled_course_ids = []
        await session.commit()
    listing = (await client.get(BASE)).json()["items"][0]
    assert not listing["can_join"] and not listing["can_report_result"]
    assert listing["can_manage"]
    assert (await client.get(f"{BASE}/{row['id']}/join")).status_code == 404
    response = await client.post(
        f"{BASE}/{row['id']}/close",
        json={
            "expected_version": 1,
            "status": "cancelled",
            "reason": "Enrollment changed; arrange the correct appointment",
        },
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_source_staleness_and_membership_revocation_block_join(appointments):
    client, factory, actor, ids = appointments
    row = (await client.post(BASE, json=body(ids["alice"]))).json()
    async with factory() as session:
        await session.execute(
            sa.update(TeamMember)
            .where(TeamMember.user_id == actor)
            .values(status="inactive")
        )
        await session.commit()
    await as_student(client, factory, ids["alice"])
    assert (
        await client.get(f"/v2/human-interviews/me/{row['id']}/join")
    ).status_code == 409
    async with factory() as session:
        (await session.get(User, ids["alice"])).enrollment_observed_at = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
        )
        await session.commit()
    assert (await client.get("/v2/human-interviews/me")).json()["total"] == 0
    assert (
        await client.get(f"/v2/human-interviews/me/{row['id']}/join")
    ).status_code == 404


@pytest.mark.asyncio
async def test_meeting_is_not_practice_consent_and_concurrent_retry_is_idempotent(
    appointments,
):
    client, factory, actor, ids = appointments
    request = body(ids["no_consent"])
    responses = await asyncio.gather(
        client.post(BASE, json=request), client.post(BASE, json=request)
    )
    assert sorted(r.status_code for r in responses) == [200, 201]
    assert responses[0].json()["id"] == responses[1].json()["id"]
