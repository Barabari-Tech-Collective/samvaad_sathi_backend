"""Placement review lifecycle with real paired grants and opportunity consent."""

import asyncio
import datetime
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from .test_fixed_question_rounds import db, pytestmark  # noqa: F401
from .test_course_reporting import reporting  # noqa: F401
from src.api.routes import course_shortlists as routes
from src.models.db.user import User
from src.models.db.team_member import TeamMember
from src.models.db.interview import Interview
from src.models.db.practice_governance import (
    PracticeConsent,
    PracticeShortlist,
    PracticeShortlistEntry,
)

BASE = "/v2/course-shortlists/courses/course-a"


@pytest.fixture
async def placements(reporting):
    client, factory, actor_id, ids = reporting
    client._transport.app.include_router(routes.router)
    async with factory() as session:
        actor = await session.get(User, actor_id)
        actor.feature_permissions = {
            "placement_shortlists": ["read", "create", "update", "publish", "delete"]
        }
        await session.execute(
            sa.update(TeamMember)
            .where(
                TeamMember.user_id == actor_id,
                TeamMember.course_ids.contains(["course-a"]),
            )
            .values(access_role="placement staff")
        )
        now = datetime.datetime.now(datetime.timezone.utc)
        for student_id in ids.values():
            session.add(
                PracticeConsent(
                    user_id=student_id,
                    purpose="opportunity_readiness",
                    granted=True,
                    policy_version="test",
                    expires_at=now + datetime.timedelta(days=7),
                )
            )
        await session.execute(sa.update(Interview).values(completed_at=now))
        await session.commit()
    yield client, factory, actor_id, ids


def body(**changes):
    return {
        "request_key": str(uuid4()),
        "name": "Engineering review",
        "opportunity": "Developer internship",
        "role": "Junior developer",
        "maximum_evidence_age_days": 90,
        "minimum_completed_interviews": 3,
        "expires_at": (
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)
        ).isoformat(),
        **changes,
    }


async def make(client):
    response = await client.post(BASE, json=body())
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def decide(client, shortlist_id, student_id, version=1, decision="include"):
    return await client.put(
        f"{BASE}/{shortlist_id}/decision",
        json={
            "student_id": student_id,
            "expected_version": version,
            "decision": decision,
            "reason": "Reviewed the relevant student context",
        },
    )


@pytest.mark.asyncio
async def test_creation_retry_candidates_and_human_decision_lifecycle(placements):
    client, factory, _, ids = placements
    request = body()
    created = await client.post(BASE, json=request)
    assert created.status_code == 201, created.text
    shortlist_id = created.json()["id"]
    retry = await client.post(BASE, json=request)
    assert retry.status_code == 200 and retry.json()["id"] == shortlist_id
    assert (
        await client.post(BASE, json={**request, "name": "Changed request"})
    ).status_code == 409
    detail = await client.get(f"{BASE}/{shortlist_id}")
    assert detail.status_code == 200, detail.text
    data = detail.json()
    assert {item["student"]["id"] for item in data["items"]} == {
        ids["alice"],
        ids["zero"],
        ids["literal"],
        ids["no_consent"],
    }
    # Coaching consent is deliberately not the placement sharing decision.
    alice = next(
        item for item in data["items"] if item["student"]["id"] == ids["alice"]
    )
    assert (
        alice["evidence"]["completed_practice"] == 2
        and alice["evidence"]["state"] == "insufficient"
    )
    assert alice["decision"] is None
    assert set(alice["evidence"]) == {
        "completed_practice",
        "latest_completed_at",
        "state",
    }
    assert detail.headers["cache-control"] == "no-store"
    response = await decide(client, shortlist_id, ids["zero"])
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    assert (await decide(client, shortlist_id, ids["alice"])).status_code == 409
    finalized = await client.post(
        f"{BASE}/{shortlist_id}/finalize",
        json={"expected_version": 2, "reason": "Reviewed the explicit human decisions"},
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["status"] == "locked" and finalized.json()["version"] == 3
    assert (await decide(client, shortlist_id, ids["alice"], 3)).status_code == 409
    locked = (await client.get(f"{BASE}/{shortlist_id}")).json()
    assert (
        len(locked["items"]) == 1 and locked["items"][0]["student"]["id"] == ids["zero"]
    )
    assert locked["items"][0]["decision_evidence"]["state"] == "missing"


@pytest.mark.asyncio
async def test_sharing_withdrawal_hides_decision_and_blocks_finalization_then_archive(
    placements,
):
    client, factory, _, ids = placements
    shortlist_id = await make(client)
    assert (await decide(client, shortlist_id, ids["alice"])).status_code == 200
    async with factory() as session:
        await session.execute(
            sa.update(PracticeConsent)
            .where(
                PracticeConsent.user_id == ids["alice"],
                PracticeConsent.purpose == "opportunity_readiness",
            )
            .values(withdrawn_at=datetime.datetime.now(datetime.timezone.utc))
        )
        await session.commit()
    detail = (await client.get(f"{BASE}/{shortlist_id}")).json()
    assert ids["alice"] not in {item["student"]["id"] for item in detail["items"]}
    assert (await decide(client, shortlist_id, ids["alice"], 2)).status_code == 404
    response = await client.post(
        f"{BASE}/{shortlist_id}/finalize",
        json={
            "expected_version": 2,
            "reason": "Finalize after checking current access",
        },
    )
    assert response.status_code == 409 and "silently removed" in response.text
    archived = await client.post(
        f"{BASE}/{shortlist_id}/archive",
        json={"expected_version": 2, "reason": "Replace draft after changed sharing"},
    )
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    async with factory() as session:
        assert (
            await session.execute(
                sa.select(sa.func.count()).select_from(PracticeShortlistEntry)
            )
        ).scalar_one() == 1


@pytest.mark.asyncio
async def test_role_course_pairing_explicit_permissions_and_legacy_rows(placements):
    client, factory, actor_id, ids = placements
    shortlist_id = await make(client)
    assert (
        await client.get("/v2/course-shortlists/courses/course-b")
    ).status_code == 403
    assert (await decide(client, shortlist_id, ids["wrong_college"])).status_code == 404
    assert (await decide(client, shortlist_id, ids["wrong_course"])).status_code == 404
    async with factory() as session:
        session.add(
            PracticeShortlist(
                name="Legacy unscoped",
                opportunity="Old opportunity",
                evidence_rules={},
                created_by=actor_id,
                expires_at=datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(days=7),
            )
        )
        await session.commit()
    assert (await client.get(BASE)).json()["total"] == 1
    for policy in [None, {"placement_shortlists": ["read"]}]:
        async with factory() as session:
            (await session.get(User, actor_id)).feature_permissions = policy
            await session.commit()
        assert (await client.post(BASE, json=body())).status_code == 403
    assert (await client.get(BASE)).status_code == 200
    async with factory() as session:
        await session.execute(
            sa.update(TeamMember)
            .where(TeamMember.user_id == actor_id)
            .values(access_role="facilitator")
        )
        await session.commit()
    assert (await client.get(BASE)).status_code == 403


@pytest.mark.asyncio
async def test_empty_and_expired_review_cannot_finalize(placements):
    client, factory, _, ids = placements
    shortlist_id = await make(client)
    assert (
        await client.post(
            f"{BASE}/{shortlist_id}/finalize",
            json={
                "expected_version": 1,
                "reason": "Reviewed the explicit human decisions",
            },
        )
    ).status_code == 409
    async with factory() as session:
        (await session.get(PracticeShortlist, shortlist_id)).expires_at = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
        )
        await session.commit()
    assert (await decide(client, shortlist_id, ids["zero"])).status_code == 409
    assert (
        await client.post(
            f"{BASE}/{shortlist_id}/archive",
            json={"expected_version": 1, "reason": "Replace the expired review draft"},
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_decision_and_finalize_race_do_not_silently_overwrite(placements):
    client, factory, _, ids = placements
    shortlist_id = await make(client)
    assert (await decide(client, shortlist_id, ids["alice"])).status_code == 200
    results = await asyncio.gather(
        decide(client, shortlist_id, ids["zero"], 2),
        client.post(
            f"{BASE}/{shortlist_id}/finalize",
            json={"expected_version": 2, "reason": "Finalize the reviewed decisions"},
        ),
    )
    assert sorted(response.status_code for response in results) == [200, 409]
    async with factory() as session:
        row = await session.get(PracticeShortlist, shortlist_id)
        assert row.version == 3
        total = (
            await session.execute(
                sa.select(sa.func.count()).select_from(PracticeShortlistEntry)
            )
        ).scalar_one()
        assert total == (1 if row.status == "locked" else 2)


@pytest.mark.asyncio
async def test_migration_triggers_seal_finalized_parent_and_decisions(placements):
    client, factory, _, ids = placements
    # Install the exact trigger SQL from the migration on the disposable model
    # schema; regular migration/schema tests also execute the full Alembic chain.
    migration = (
        Path(__file__).resolve().parents[2]
        / "src/repository/migrations/versions/2026_10_19_0800-f4a5b6c7d8e9_course_shortlists.py"
    )
    spec = importlib.util.spec_from_file_location("shortlist_migration", migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements = []
    module.op = SimpleNamespace(
        add_column=lambda *a: None,
        create_index=lambda *a: None,
        create_unique_constraint=lambda *a: None,
        execute=statements.append,
    )
    module.upgrade()
    async with factory() as session:
        await session.execute(
            sa.text("DROP FUNCTION IF EXISTS protect_course_shortlist_entry() CASCADE")
        )
        await session.execute(
            sa.text("DROP FUNCTION IF EXISTS protect_course_shortlist() CASCADE")
        )
        for statement in statements:
            await session.execute(sa.text(statement))
        await session.commit()
    shortlist_id = await make(client)
    assert (await decide(client, shortlist_id, ids["zero"])).status_code == 200
    assert (
        await client.post(
            f"{BASE}/{shortlist_id}/finalize",
            json={"expected_version": 2, "reason": "Finalize the reviewed decisions"},
        )
    ).status_code == 200
    async with factory() as session:
        for query in [
            sa.update(PracticeShortlist)
            .where(PracticeShortlist.id == shortlist_id)
            .values(name="Tampered"),
            sa.update(PracticeShortlistEntry)
            .where(PracticeShortlistEntry.shortlist_id == shortlist_id)
            .values(reason="Tampered"),
            sa.delete(PracticeShortlist).where(PracticeShortlist.id == shortlist_id),
        ]:
            with pytest.raises(DBAPIError, match="immutable"):
                await session.execute(query)
            await session.rollback()

        # A privacy deletion is not a business edit to the finalized review.
        await session.execute(sa.delete(User).where(User.id == ids["zero"]))
        await session.commit()
        assert (
            await session.execute(
                sa.select(sa.func.count()).select_from(PracticeShortlistEntry)
            )
        ).scalar_one() == 0
        assert (await session.get(PracticeShortlist, shortlist_id)).status == "locked"
