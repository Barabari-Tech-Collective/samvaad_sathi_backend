"""Real SQL and HTTP permission/lifecycle tests for formal opportunity selection."""

import asyncio
import datetime as dt
import uuid
import importlib.util
from pathlib import Path
import fastapi
import httpx
import pytest
import sqlalchemy as sa
from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.api.routes import (
    opportunity_selection as routes,
    opportunities,
    human_interviews,
)
from src.models.db.opportunity import Opportunity, OpportunityCandidate
from src.models.db.opportunity_selection import (
    OpportunitySelection,
    OpportunitySelectionEntry,
)
from src.models.db.user import User
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.interview import Interview
from src.models.db.human_interview import HumanInterview
from src.services import opportunity_selection as service
from src.api.routes import staff_tasks
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


@pytest.fixture
async def selection(db):
    engine, factory, _ = db
    # Exercise every HTTP transition with the real migration's immutability triggers.
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = (
        Path(__file__).resolve().parents[2]
        / "src/repository/migrations/versions/2026_10_31_0100-c3d4e5f6a7b9_opportunity_selection.py"
    )
    spec = importlib.util.spec_from_file_location("selection_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with engine.begin() as conn:

        def install(sync_conn):
            from sqlalchemy import text

            from src.models.db.opportunity_handoff import (
                OpportunityHandoff,
                OpportunityHandoffCandidate,
            )

            # The company handoff points at selections, so it is rebuilt around the migration.
            sync_conn.execute(
                text("DROP TABLE IF EXISTS opportunity_handoff_candidate, opportunity_handoff CASCADE")
            )
            with Operations.context(MigrationContext.configure(sync_conn)):
                migration.downgrade()
                migration.upgrade()
            OpportunityHandoff.__table__.create(sync_conn)
            OpportunityHandoffCandidate.__table__.create(sync_conn)

        await conn.run_sync(install)
    now = dt.datetime.now(dt.timezone.utc)
    async with factory() as s:
        users = {}
        for name, role in [
            ("lead", True),
            ("member", True),
            ("student", False),
            ("other", False),
        ]:
            user = User(
                email=f"{name}@selection.test",
                name=name,
                password_hash="test",
                enrolled_course_ids=["course-a"],
                enrollment_source="sampark",
                enrollment_observed_at=now,
                feature_permissions=(
                    {
                        "placement_shortlists": (
                            ["read", "update", "publish"]
                            if name == "lead"
                            else ["read", "update"]
                        )
                    }
                    if role
                    else {}
                ),
            )
            s.add(user)
            await s.flush()
            users[name] = user.id
        team = Team(name="Placement")
        s.add(team)
        await s.flush()
        for name in ("lead", "member"):
            s.add(
                TeamMember(
                    team_id=team.id,
                    user_id=users[name],
                    access_role="team member",
                    purposes=["placement"],
                    course_ids=["course-a"],
                )
            )
        opening = Opportunity(
            course_id="course-a",
            title="Developer",
            organisation="Example",
            created_by=users["lead"],
            request_key=str(uuid.uuid4()),
            request_hash="x",
            max_evidence_age_days=30,
            min_assessments=1,
            status="open",
            eligibility_version=1,
        )
        s.add(opening)
        await s.flush()
        s.add(
            OpportunityCandidate(
                opportunity_id=opening.id,
                student_id=users["student"],
                added_by=users["lead"],
            )
        )
        s.add(
            Interview(
                user_id=users["student"],
                track="Dev",
                course_id="course-a",
                interview_mode="assessment",
                status="completed",
                completed_at=now,
            )
        )
        await s.commit()
        oid = opening.id
    app = fastapi.FastAPI()
    app.include_router(routes.router)
    app.include_router(opportunities.router)
    app.include_router(human_interviews.router)
    app.include_router(staff_tasks.router)
    from src.api.routes import opportunity_handoff, readiness

    app.include_router(opportunity_handoff.router)
    app.include_router(readiness.router)

    async def session_dep():
        async with factory() as s:
            try:
                yield s
            finally:
                await s.rollback()

    async def actor(
        request: fastapi.Request, session=fastapi.Depends(get_async_session)
    ):
        return await session.get(
            User, users[request.headers.get("x-test-actor", "lead")]
        )

    app.dependency_overrides[get_async_session] = session_dep
    app.dependency_overrides[get_current_user] = actor
    app.dependency_overrides[get_current_admin_user] = actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, factory, oid, users


def change(version, **extra):
    return {
        "expected_version": version,
        "reason": "Reviewed formal evidence carefully",
        **extra,
    }


async def decide(c, base, student, version):
    return await c.put(
        base + "/decision",
        json=change(
            version,
            student_id=student,
            decision="include",
            student_feedback="Your project explanation met the role requirements.",
            next_step="Attend the next round.",
        ),
    )


async def test_member_to_lead_to_locked_to_released(selection):
    c, factory, oid, users = selection
    base = f"/v2/opportunities/{oid}/selection"
    view = (await c.get(base)).json()
    assert view["version"] == 0 and view["items"][0]["evidence"]["state"] == "ready"
    assert (
        await c.get(
            "/v2/opportunities/me/outcomes", headers={"x-test-actor": "student"}
        )
    ).json() == {"items": []}
    r = await c.put(
        base + "/recommendation",
        headers={"x-test-actor": "member"},
        json=change(0, student_id=users["student"], decision="include"),
    )
    assert r.status_code == 200, r.text
    v = r.json()["version"]
    for action in ("lock", "release", "revision"):
        payload = change(
            v, **({"request_key": str(uuid.uuid4())} if action == "release" else {})
        )
        assert (
            await c.post(
                base + "/" + action, json=payload, headers={"x-test-actor": "member"}
            )
        ).status_code == 403
    assert (
        await c.put(
            base + "/decision",
            json=change(
                v,
                student_id=users["student"],
                decision="exclude",
                student_feedback="Internal reviewer opinion",
                next_step="Contact placement.",
            ),
            headers={"x-test-actor": "member"},
        )
    ).status_code == 403
    assert (await decide(c, base, users["student"], 0)).status_code == 409
    r = await decide(c, base, users["student"], v)
    assert r.status_code == 200, r.text
    v = r.json()["version"]
    r = await c.post(base + "/lock", json=change(v))
    assert r.status_code == 200, r.text
    v = r.json()["version"]
    assert (await decide(c, base, users["student"], v)).status_code == 409
    assert (
        await c.get(
            "/v2/opportunities/me/outcomes", headers={"x-test-actor": "student"}
        )
    ).json()["items"] == []
    release = change(v, request_key=str(uuid.uuid4()))
    r = await c.post(base + "/release", json=release)
    assert r.status_code == 200, r.text
    again = await c.post(base + "/release", json=release)
    assert again.json() == r.json()
    own = await c.get(
        "/v2/opportunities/me/outcomes", headers={"x-test-actor": "student"}
    )
    assert own.status_code == 200 and own.headers["cache-control"] == "no-store"
    item = own.json()["items"][0]
    assert (
        item["decision"] == "include" and item["next_step"] == "Attend the next round."
    )
    assert (
        "reason" not in item
        and "Reviewed formal" not in own.text
        and "recommended_by" not in own.text
    )
    assert (
        await c.get("/v2/opportunities/me/outcomes", headers={"x-test-actor": "other"})
    ).json()["items"] == []
    r = await c.post(base + "/revision", json=change(r.json()["version"]))
    assert r.status_code == 200
    view = (await c.get(base)).json()
    assert view["revision"] == 2 and view["items"][0]["decision"] is None
    # Existing released result is still shown while a new draft is under review.
    assert (
        await c.get(
            "/v2/opportunities/me/outcomes", headers={"x-test-actor": "student"}
        )
    ).json()["items"][0]["revision"] == 1


async def test_evidence_and_membership_rechecked_at_lock(selection):
    c, factory, oid, users = selection
    base = f"/v2/opportunities/{oid}/selection"
    r = await decide(c, base, users["student"], 0)
    v = r.json()["version"]
    async with factory() as s:
        await s.execute(
            sa.update(Interview)
            .where(Interview.user_id == users["student"])
            .values(
                completed_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)
            )
        )
        await s.commit()
    r = await c.post(base + "/lock", json=change(v))
    assert r.status_code == 409 and "fresh formal" in r.text
    async with factory() as s:
        await s.execute(
            sa.update(User)
            .where(User.id == users["student"])
            .values(enrolled_course_ids=["another-course"])
        )
        await s.commit()
    r = await c.post(base + "/lock", json=change(v))
    assert r.status_code == 409 and "scope" in r.text
    # No candidate names or evidence leaked after membership changes.
    assert (await c.get(base)).json()["items"] == []


async def test_scheduled_human_and_mocks_never_qualify(selection):
    c, factory, oid, users = selection
    async with factory() as s:
        await s.execute(sa.update(Interview).values(interview_mode="practice"))
        now = dt.datetime.now(dt.timezone.utc)
        h = HumanInterview(
            course_id="course-a",
            opportunity_id=oid,
            student_id=users["student"],
            host_id=users["lead"],
            title="Human round",
            meet_url="https://meet.google.com/abc-defg-hij",
            starts_at=now,
            ends_at=now + dt.timedelta(minutes=30),
            request_key=str(uuid.uuid4()),
            request_hash="x",
            status="scheduled",
        )
        s.add(h)
        await s.commit()
        hid = h.id
    base = f"/v2/opportunities/{oid}/selection"
    view = (await c.get(base)).json()
    assert view["items"][0]["evidence"]["count"] == 0
    assert (await decide(c, base, users["student"], 0)).status_code == 409
    async with factory() as s:
        h = await s.get(HumanInterview, hid)
        h.status = "completed"
        h.closed_at = dt.datetime.now(dt.timezone.utc)
        await s.commit()
    assert (await c.get(base)).json()["items"][0]["evidence"]["count"] == 0
    async with factory() as s:
        h = await s.get(HumanInterview, hid)
        h.result = "next_round"
        await s.commit()
    view = (await c.get(base)).json()
    assert view["items"][0]["evidence"]["human_round_ids"] == [hid]
    r = await decide(c, base, users["student"], 0)
    assert r.status_code == 200
    assert (await c.get(f"/v2/opportunities/{oid}")).json()["candidates"][0][
        "state"
    ] == "ready"
    # A correction to an already-reviewed human result requires another lead review.
    async with factory() as s:
        h = await s.get(HumanInterview, hid)
        h.result = "not_selected"
        await s.commit()
    locked = await c.post(base + "/lock", json=change(r.json()["version"]))
    assert locked.status_code == 409 and "evidence changed" in locked.text


async def test_rules_change_and_recommendation_require_review(selection):
    c, factory, oid, users = selection
    base = f"/v2/opportunities/{oid}/selection"
    r = await decide(c, base, users["student"], 0)
    v = r.json()["version"]
    r = await c.put(
        base + "/recommendation",
        json=change(v, student_id=users["student"], decision="needs_evidence"),
        headers={"x-test-actor": "member"},
    )
    v = r.json()["version"]
    assert (await c.get(base)).json()["items"][0]["decision"] is None
    async with factory() as s:
        opening = await s.get(Opportunity, oid)
        opening.min_assessments = 2
        opening.eligibility_version += 1
        await s.commit()
    assert (await decide(c, base, users["student"], v)).status_code == 409
    assert (await c.post(base + "/revision", json=change(v))).status_code == 200


async def test_scope_denials_and_release_revalidation(selection):
    c, factory, oid, users = selection
    base = f"/v2/opportunities/{oid}/selection"
    assert (await c.get(base, headers={"x-test-actor": "other"})).status_code == 403
    r = await decide(c, base, users["student"], 0)
    r = await c.post(base + "/lock", json=change(r.json()["version"]))
    v = r.json()["version"]
    async with factory() as s:
        await s.execute(
            sa.update(TeamMember)
            .where(TeamMember.user_id == users["lead"])
            .values(course_ids=["course-b"])
        )
        await s.commit()
    assert (
        await c.post(base + "/release", json=change(v, request_key=str(uuid.uuid4())))
    ).status_code == 403
    assert (
        await c.get(
            "/v2/opportunities/me/outcomes", headers={"x-test-actor": "student"}
        )
    ).json()["items"] == []


async def test_concurrent_writes_and_old_revision_cannot_overwrite(selection):
    c, factory, oid, users = selection
    base = f"/v2/opportunities/{oid}/selection"
    a, b = await asyncio.gather(
        decide(c, base, users["student"], 0), decide(c, base, users["student"], 0)
    )
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    v = (a if a.status_code == 200 else b).json()["version"]
    revision = await c.post(base + "/revision", json=change(v))
    assert revision.status_code == 200 and revision.json()["version"] > v
    assert (await decide(c, base, users["student"], v)).status_code == 409
    assert (await c.get(base)).json()["items"][0]["decision"] is None


async def test_frozen_rows_are_protected_in_the_database(selection):
    c, factory, oid, users = selection
    base = f"/v2/opportunities/{oid}/selection"
    r = await decide(c, base, users["student"], 0)
    r = await c.post(base + "/lock", json=change(r.json()["version"]))
    assert r.status_code == 200, r.text
    sid = r.json()["selection_id"]
    async with factory() as s:
        for statement in (
            sa.update(OpportunitySelection)
            .where(OpportunitySelection.id == sid)
            .values(snapshot={}),
            sa.delete(OpportunitySelection).where(OpportunitySelection.id == sid),
            sa.update(OpportunitySelectionEntry)
            .where(OpportunitySelectionEntry.selection_id == sid)
            .values(student_feedback="Changed silently"),
            sa.delete(OpportunitySelectionEntry).where(
                OpportunitySelectionEntry.selection_id == sid
            ),
            sa.insert(OpportunitySelectionEntry).values(
                selection_id=sid, student_id=users["other"]
            ),
        ):
            with pytest.raises(sa.exc.DBAPIError, match="immutable"):
                async with s.begin_nested():
                    await s.execute(statement)
    released = await c.post(
        base + "/release",
        json=change(r.json()["version"], request_key=str(uuid.uuid4())),
    )
    assert released.status_code == 200, released.text
    async with factory() as s:
        with pytest.raises(sa.exc.DBAPIError, match="immutable"):
            async with s.begin_nested():
                await s.execute(
                    sa.update(OpportunitySelection)
                    .where(OpportunitySelection.id == sid)
                    .values(release_reason="Changed silently")
                )


async def test_release_rechecks_rules_and_candidate_membership(selection):
    c, factory, oid, users = selection
    base = f"/v2/opportunities/{oid}/selection"
    r = await decide(c, base, users["student"], 0)
    r = await c.post(base + "/lock", json=change(r.json()["version"]))
    body = change(r.json()["version"], request_key=str(uuid.uuid4()))
    async with factory() as s:
        opening = await s.get(Opportunity, oid)
        opening.eligibility_version += 1
        await s.commit()
    blocked = await c.post(base + "/release", json=body)
    assert blocked.status_code == 409 and "Rules changed" in blocked.text
    async with factory() as s:
        opening = await s.get(Opportunity, oid)
        opening.eligibility_version -= 1
        await s.execute(
            sa.delete(OpportunityCandidate).where(
                OpportunityCandidate.opportunity_id == oid
            )
        )
        await s.commit()
    blocked = await c.post(base + "/release", json=body)
    assert blocked.status_code == 409 and "membership" in blocked.text
    assert (
        await c.get(
            "/v2/opportunities/me/outcomes", headers={"x-test-actor": "student"}
        )
    ).json()["items"] == []


async def test_staff_queue_changes_with_selection_owner_and_state(selection):
    c, factory, oid, users = selection
    base = f"/v2/opportunities/{oid}/selection"

    async def task(actor):
        async with factory() as s:
            user = await s.get(User, users[actor])
            return await staff_tasks._selection_tasks(user, s, [])

    assert (await task("member"))[0]["title"] == "Opportunity recommendations pending"
    assert (await task("lead"))[0]["title"] == "Opportunity decisions awaiting review"
    assert await task("other") == []
    r = await c.put(
        base + "/recommendation",
        headers={"x-test-actor": "member"},
        json=change(0, student_id=users["student"], decision="include"),
    )
    assert await task("member") == []
    r = await decide(c, base, users["student"], r.json()["version"])
    assert (await task("lead"))[0]["title"] == "Opportunity ready for lock review"
    r = await c.post(base + "/lock", json=change(r.json()["version"]))
    assert (await task("lead"))[0]["title"] == "Opportunity outcomes awaiting release"
    assert await task("member") == []
    r = await c.post(
        base + "/release",
        json=change(r.json()["version"], request_key=str(uuid.uuid4())),
    )
    assert r.status_code == 200, r.text
    assert await task("lead") == []


async def test_history_is_read_only_and_retains_frozen_evidence(selection):
    c, factory, oid, users = selection
    base = f"/v2/opportunities/{oid}/selection"
    r = await decide(c, base, users["student"], 0)
    r = await c.post(base + "/lock", json=change(r.json()["version"]))
    frozen = (await c.get(base)).json()["items"]
    r = await c.post(base + "/revision", json=change(r.json()["version"]))
    assert r.status_code == 200
    async with factory() as s:
        await s.execute(
            sa.delete(OpportunityCandidate).where(
                OpportunityCandidate.opportunity_id == oid
            )
        )
        await s.commit()
    history = await c.get(base + "?revision=1")
    assert history.status_code == 200, history.text
    data = history.json()
    assert data["historical"] and data["items"] == frozen
    assert not any(
        data[k]
        for k in (
            "can_recommend",
            "can_decide",
            "can_lock",
            "can_release",
            "can_revise",
        )
    )
    assert (await c.get(base + "?revision=99")).status_code == 404
    async with factory() as s:
        await s.execute(
            sa.update(User)
            .where(User.id == users["student"])
            .values(enrolled_course_ids=[])
        )
        await s.commit()
    assert (await c.get(base + "?revision=1")).json()["items"] == []


async def test_board_counts_people_by_stage(selection):
    c, factory, oid, users = selection
    r = await c.get("/v2/opportunities/board", headers={"x-test-actor": "lead"})
    assert r.status_code == 200, r.text
    row = next(i for i in r.json()["items"] if i["id"] == oid)
    assert row["people"] == row["need"] + row["todo"] + row["included"] + row["excluded"]
    assert row["selection"] == "draft" and row["sent"] is False
