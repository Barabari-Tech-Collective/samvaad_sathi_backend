"""Real SQL tests for linking resume-screening runs to an opening (pack F25)."""

import datetime as dt
import uuid

import fastapi
import httpx
import pytest

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes import opportunities
from src.models.db.bulk_ats import BulkATSBatch
from src.models.db.opportunity import Opportunity
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


@pytest.fixture
async def screening(db):
    _, factory, _ = db
    async with factory() as s:
        users = {}
        for name in ("lead", "colleague"):
            user = User(
                email=f"{name}@screening.test",
                name=name,
                password_hash="test",
                feature_permissions={"placement_shortlists": ["read", "update"]},
            )
            s.add(user)
            await s.flush()
            users[name] = user.id
        team = Team(name="Placement")
        s.add(team)
        await s.flush()
        for name in users:
            s.add(
                TeamMember(
                    team_id=team.id,
                    user_id=users[name],
                    access_role="team member",
                    purposes=["placement"],
                    course_ids=["course-a"],
                )
            )
        openings = []
        for title in ("Analyst", "Support"):
            o = Opportunity(
                course_id="course-a",
                title=title,
                organisation="Example",
                created_by=users["lead"],
                request_key=str(uuid.uuid4()),
                request_hash="x",
                max_evidence_age_days=30,
                min_assessments=1,
                status="open",
                eligibility_version=1,
            )
            s.add(o)
            await s.flush()
            openings.append(o.id)
        batches = {}
        for name, owner in (("mine", "lead"), ("theirs", "colleague")):
            b = BulkATSBatch(name=f"{name} run", role_name="Analyst", created_by=users[owner])
            s.add(b)
            await s.flush()
            batches[name] = b.id
        await s.commit()
    app = fastapi.FastAPI()
    app.include_router(opportunities.router)

    async def session_dep():
        async with factory() as s:
            try:
                yield s
            finally:
                await s.rollback()

    async def actor(request: fastapi.Request, session=fastapi.Depends(get_async_session)):
        return await session.get(User, users[request.headers.get("x-test-actor", "lead")])

    app.dependency_overrides[get_async_session] = session_dep
    app.dependency_overrides[get_current_admin_user] = actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, factory, openings, batches


async def test_link_move_guard_and_unlink(screening):
    c, factory, (analyst, support), batches = screening
    link = f"/v2/opportunities/{analyst}/screening/{batches['mine']}"
    assert (await c.put(link)).status_code == 200
    detail = (await c.get(f"/v2/opportunities/{analyst}")).json()
    assert [b["name"] for b in detail["screening"]] == ["mine run"]
    # Already on another opening: refused, not silently moved.
    r = await c.put(f"/v2/opportunities/{support}/screening/{batches['mine']}")
    assert r.status_code == 409
    # Someone else's run cannot be attached or detached.
    assert (await c.put(f"/v2/opportunities/{analyst}/screening/{batches['theirs']}")).status_code == 404
    assert (await c.delete(f"/v2/opportunities/{support}/screening/{batches['mine']}")).status_code == 404
    assert (await c.delete(link)).status_code == 200
    async with factory() as s:
        assert (await s.get(BulkATSBatch, batches["mine"])).opportunity_id is None
    assert (await c.put(f"/v2/opportunities/{support}/screening/{batches['mine']}")).status_code == 200
