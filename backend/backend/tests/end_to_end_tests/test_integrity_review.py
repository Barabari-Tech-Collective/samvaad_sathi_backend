"""Real SQL/HTTP integrity decisions, scoped evidence and learner appeals."""

import asyncio
import datetime as dt
from uuid import uuid4
import fastapi
import pytest
import sqlalchemy as sa
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401
from .test_course_reporting import reporting  # noqa: F401
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.api.routes import integrity_review as routes
from src.models.db.user import User
from src.models.db.interview import Interview
from src.models.db.integrity_case import IntegrityCase
from src.models.db.team_member import TeamMember

BASE = "/v2/integrity/courses/course-a/cases"


@pytest.fixture
async def review(reporting):
    client, factory, actor, ids = reporting
    app = client._transport.app
    app.include_router(routes.router)
    async with factory() as session:
        user = await session.get(User, actor)
        user.feature_permissions = {
            "integrity_review": [
                "read",
                "create",
                "update",
                "publish",
                "resolve_appeal",
            ]
        }
        independent = User(
            email="independent@example.test",
            name="Independent",
            password_hash="test",
            is_admin=True,
        )
        session.add(independent)
        await session.flush()
        ids["independent"] = independent.id
        sitting = await session.scalar(
            sa.select(Interview).where(
                Interview.user_id == ids["alice"],
                Interview.interview_mode == "assessment",
            )
        )
        sitting.integrity_events = [
            {
                "eventId": "signal-one",
                "reason": "tab_switch",
                "recordedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
        ]
        ids["interview"] = sitting.id
        await session.commit()
    yield client, factory, actor, ids


async def principal(client, factory, user_id):
    async def current(session=fastapi.Depends(get_async_session)):
        return await session.get(User, user_id)

    client._transport.app.dependency_overrides[get_current_user] = current


def opening(interview, **overrides):
    return {
        "request_key": str(uuid4()),
        "interview_id": interview,
        "source": "technical_context",
        "reason": "A technical incident needs a fair human review.",
        "shared_evidence_confirmed": True,
        **overrides,
    }


def action(row, name, **overrides):
    return {
        "request_key": str(uuid4()),
        "expected_version": row["version"],
        "action": name,
        "reason": "Reviewed the shared evidence and technical context carefully.",
        "shared_evidence_confirmed": True,
        **overrides,
    }


@pytest.mark.asyncio
async def test_full_review_context_appeal_and_independent_resolution(review):
    client, factory, actor, ids = review
    body = opening(ids["interview"])
    opened = await client.post(BASE, json=body)
    assert opened.status_code == 200, opened.text
    row = opened.json()
    assert (await client.post(BASE, json=body)).json()["version"] == 1
    assert (await client.post(BASE, json=opening(ids["interview"]))).status_code == 409
    assert opened.headers["cache-control"] == "no-store"
    # Browser signals alone cannot produce an adverse finding.
    assert (
        await client.post(
            f"{BASE}/{row['id']}/actions", json=action(row, "decide", outcome="concern")
        )
    ).status_code == 422
    row = (
        await client.post(
            f"{BASE}/{row['id']}/actions",
            json=action(row, "add_evidence", source="interviewer_observation"),
        )
    ).json()
    row = (
        await client.post(
            f"{BASE}/{row['id']}/actions", json=action(row, "decide", outcome="concern")
        )
    ).json()
    assert row["state"] == "decided"
    await principal(client, factory, ids["alice"])
    # Historical appeal survives enrollment revocation.
    async with factory() as session:
        (await session.get(User, ids["alice"])).enrolled_course_ids = []
        await session.commit()
    learner = (await client.get("/v2/integrity/me/cases")).json()["items"][0]
    assert learner["can_appeal"]
    assert "actor_id" not in str(learner) and "request_hash" not in str(learner)
    request = action(learner, "appeal")
    del request["shared_evidence_confirmed"]
    appeal = await client.post(
        f"/v2/integrity/me/cases/{row['id']}/actions", json=request
    )
    assert appeal.status_code == 200, appeal.text
    assert (
        await client.post(f"/v2/integrity/me/cases/{row['id']}/actions", json=request)
    ).json()["version"] == appeal.json()["version"]
    await principal(client, factory, actor)
    resolve = action(appeal.json(), "resolve_appeal", outcome="clear")
    assert (
        await client.post(f"{BASE}/{row['id']}/actions", json=resolve)
    ).status_code == 409
    await principal(client, factory, ids["independent"])
    resolved = await client.post(f"{BASE}/{row['id']}/actions", json=resolve)
    assert resolved.status_code == 200, resolved.text
    assert (
        resolved.json()["state"] == "resolved" and resolved.json()["outcome"] == "clear"
    )
    async with factory() as session:
        interview = await session.get(Interview, ids["interview"])
        assert interview.status == "completed" and not interview.proctoring_terminated


@pytest.mark.asyncio
async def test_scope_and_permission_revocation_and_ownership(review):
    client, factory, actor, ids = review
    row = (await client.post(BASE, json=opening(ids["interview"]))).json()
    for policy in [None, {}, {"integrity_review": ["read"]}]:
        async with factory() as session:
            (await session.get(User, actor)).feature_permissions = policy
            await session.commit()
        assert (
            await client.post(
                f"{BASE}/{row['id']}/actions",
                json=action(row, "decide", outcome="clear"),
            )
        ).status_code == 403
    await principal(client, factory, ids["zero"])
    assert (await client.get("/v2/integrity/me/cases")).json()["items"] == []
    request = action(row, "appeal")
    del request["shared_evidence_confirmed"]
    assert (
        await client.post(f"/v2/integrity/me/cases/{row['id']}/actions", json=request)
    ).status_code == 404
    await principal(client, factory, actor)
    async with factory() as session:
        user = await session.get(User, actor)
        user.feature_permissions = {"integrity_review": ["read", "publish"]}
        members = (
            await session.scalars(
                sa.select(TeamMember).where(TeamMember.user_id == actor)
            )
        ).all()
        for member in members:
            member.status = "deleted"
        await session.commit()
    assert (await client.get(BASE)).status_code in {403, 404}


@pytest.mark.asyncio
async def test_stale_versions_replay_conflicts_and_expired_appeal(review):
    client, factory, actor, ids = review
    row = (await client.post(BASE, json=opening(ids["interview"]))).json()
    decision = action(row, "decide", outcome="inconclusive")
    done = await client.post(f"{BASE}/{row['id']}/actions", json=decision)
    assert done.status_code == 200, done.text
    assert (
        await client.post(f"{BASE}/{row['id']}/actions", json=decision)
    ).status_code == 200
    assert (
        await client.post(
            f"{BASE}/{row['id']}/actions", json={**decision, "outcome": "clear"}
        )
    ).status_code == 409
    assert (
        await client.post(
            f"{BASE}/{row['id']}/actions",
            json=action(row, "add_evidence", source="technical_context"),
        )
    ).status_code == 409
    async with factory() as session:
        case = await session.get(IntegrityCase, row["id"])
        case.appeal_deadline = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
            seconds=1
        )
        await session.commit()
    await principal(client, factory, ids["alice"])
    request = action(done.json(), "appeal")
    del request["shared_evidence_confirmed"]
    assert (
        await client.post(f"/v2/integrity/me/cases/{row['id']}/actions", json=request)
    ).status_code == 409


@pytest.mark.asyncio
async def test_concurrent_decisions_accept_exactly_one(review):
    client, factory, actor, ids = review
    row = (await client.post(BASE, json=opening(ids["interview"]))).json()
    replies = await asyncio.gather(
        *[
            client.post(
                f"{BASE}/{row['id']}/actions",
                json=action(row, "decide", outcome=outcome),
            )
            for outcome in ["clear", "inconclusive"]
        ]
    )
    assert sorted(r.status_code for r in replies) == [200, 409]


@pytest.mark.asyncio
async def test_practice_active_and_wrong_course_cannot_open_cases(review):
    client, factory, actor, ids = review
    async with factory() as session:
        rows = (
            await session.scalars(
                sa.select(Interview).where(
                    Interview.user_id == ids["alice"],
                    Interview.interview_mode == "practice",
                )
            )
        ).all()
        practice = rows[0].id
    assert (await client.post(BASE, json=opening(practice))).status_code == 404
    assert (
        await client.post(BASE, json=opening(ids["interview"], reason="   "))
    ).status_code == 422
    assert (
        await client.post(
            BASE, json=opening(ids["interview"], shared_evidence_confirmed=False)
        )
    ).status_code == 422
    assert (
        await client.post(
            "/v2/integrity/courses/course-b/cases", json=opening(ids["interview"])
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_redaction_is_admin_only_and_preserves_private_audit(review):
    client, factory, actor, ids = review
    row = (
        await client.post(
            BASE,
            json=opening(
                ids["interview"], reason="Third-party private details need removal."
            ),
        )
    ).json()
    request = action(
        row,
        "redact",
        target_index=0,
        reason="Third-party details removed. Technical issue remains under review.",
    )
    assert (
        await client.post(f"{BASE}/{row['id']}/actions", json=request)
    ).status_code == 409
    await principal(client, factory, ids["independent"])
    response = await client.post(f"{BASE}/{row['id']}/actions", json=request)
    assert response.status_code == 200, response.text
    assert "Third-party private details" not in str(response.json())
    async with factory() as session:
        saved = await session.get(IntegrityCase, row["id"])
        assert "Third-party private details" in saved.history[0]["reason"]
    await principal(client, factory, ids["alice"])
    learner = (await client.get("/v2/integrity/me/cases")).json()
    assert "Third-party private details" not in str(learner)
