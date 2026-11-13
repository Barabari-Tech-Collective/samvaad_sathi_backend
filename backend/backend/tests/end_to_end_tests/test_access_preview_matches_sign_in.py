"""The Access page previews exactly what a person's own sign-in sends."""

import fastapi
import httpx
import pytest

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes import staff_access
from src.api.routes.users import _extract_primary_membership_and_claims
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User

from .test_fixed_question_rounds import db, pytestmark  # noqa: F401

ROLES = [
    ("Program", "program manager"),
    ("Program", "facilitator"),
    ("Program", "senior facilitator"),
    ("Placement", "placement staff"),
    ("Placement", "recruiter"),
    ("Program", "junior facilitator"),
    ("Program", "member"),
]


@pytest.fixture
async def api(db):  # noqa: F811
    _, factory, _ = db
    async with factory() as s:
        admin = User(email="boss@example.com", name="Boss", password_hash="x", is_admin=True)
        s.add(admin)
        teams = {n: Team(name=n, status="active") for n in ("Program", "Placement")}
        s.add_all(teams.values())
        await s.flush()
        people = {}
        for i, (team, role) in enumerate(ROLES):
            u = User(email=f"p{i}@example.com", name=f"P{i} {role}", password_hash="x")
            s.add(u)
            await s.flush()
            s.add(TeamMember(team_id=teams[team].id, user_id=u.id, access_role=role, status="active", course_ids=[]))
            people[role] = u.id
        await s.commit()
        admin_id = admin.id
    app = fastapi.FastAPI()
    app.include_router(staff_access.router)

    async def session_dep():
        async with factory() as s:
            yield s

    async def actor(session=fastapi.Depends(get_async_session)):
        return await session.get(User, admin_id)

    app.dependency_overrides[get_async_session] = session_dep
    app.dependency_overrides[get_current_admin_user] = actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        yield client, factory, people


async def test_preview_equals_the_persons_own_sign_in(api):
    client, factory, people = api
    res = await client.get("/v2/staff-access/people")
    items = {p["id"]: p for p in res.json()["items"]}
    assert len(items) >= len(ROLES)
    async with factory() as s:
        from src.api.dependencies.admin import is_bulk_ats_user
        from src.api.routes.users import get_user_active_memberships as memberships_of

        for role, uid in people.items():
            user = await s.get(User, uid)
            real = _extract_primary_membership_and_claims(user, await memberships_of(uid, s))
            preview = items[uid]["sign_in_preview"]
            for key in (
                "is_team_lead", "can_create_roles", "can_access_ats", "can_access_dashboard",
                "can_access_students", "can_access_colleges", "can_access_interviews",
                "can_access_practice_interview", "access_role", "course_limited",
            ):
                assert preview[key] == real[key], (role, key)
            assert preview["can_use_bulk_ats"] == is_bulk_ats_user(user)


async def test_every_listed_person_has_a_preview_with_the_menu_flags(api):
    client, _, people = api
    items = {p["id"]: p["sign_in_preview"] for p in (await client.get("/v2/staff-access/people")).json()["items"]}
    for role, uid in people.items():
        preview = items[uid]
        for key in ("is_team_lead", "can_access_dashboard", "can_access_students", "can_use_bulk_ats", "course_limited"):
            assert isinstance(preview[key], bool), (role, key)
        assert preview["is_admin"] is False
