"""Isolated PostgreSQL + HTTP checks; authentication identity is injected."""
import fastapi
import httpx
import pytest
import sqlalchemy as sa
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.session import get_async_session
from src.api.dependencies.features import feature_gate
from src.api.routes.feature_access import router
from src.models.db.user import User
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401

@pytest.mark.asyncio
async def test_admin_assigns_personal_access_then_revokes_on_next_request(db):
    _, factory, person_id = db
    async with factory() as session:
        admin = User(email="admin@example.test",name="Admin",password_hash="test",is_admin=True)
        team = Team(name="Program")
        session.add_all([admin,team]); await session.flush()
        session.add(TeamMember(team_id=team.id,user_id=person_id,access_role="facilitator"))
        await session.commit(); admin_id=admin.id
    app=fastapi.FastAPI(); app.include_router(router)
    active_id = admin_id
    async def identity():
        async with factory() as session:
            return await session.get(User,active_id)
    async def database():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_current_user]=identity
    app.dependency_overrides[get_current_admin_user]=identity
    app.dependency_overrides[get_async_session]=database
    @app.get("/protected",dependencies=[fastapi.Depends(feature_gate("course_workspace"))])
    async def protected(): return {"ok":True}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        body={"expected_version":0,"permissions":{"course_workspace":["read"]},"reason":"Assigned coaching workspace"}
        saved=await client.put(f"/v2/feature-access/people/{person_id}",json=body)
        assert saved.status_code == 200 and saved.json()["version"] == 1
        assert (await client.put(f"/v2/feature-access/people/{person_id}",json=body)).status_code == 409
        active_id=person_id
        assert (await client.get("/protected")).status_code == 200
        assert (await client.put(f"/v2/feature-access/people/{person_id}",json=body)).status_code == 403
        active_id=admin_id
        body.update(expected_version=1,permissions={})
        assert (await client.put(f"/v2/feature-access/people/{person_id}",json=body)).status_code == 200
        active_id=person_id
        assert (await client.get("/protected")).status_code == 403
    async with factory() as session:
        events=(await session.execute(sa.select(PracticeDashboardAuditEvent).where(PracticeDashboardAuditEvent.action=="personal_feature_access_updated"))).scalars().all()
        assert len(events)==2 and events[-1].event_metadata["after"] == {}

@pytest.mark.asyncio
async def test_permissions_cannot_target_administrators(db):
    _,factory,person_id=db
    async with factory() as session:
        person=await session.get(User,person_id);person.is_admin=True
        team=Team(name="Admin team");session.add(team);await session.flush()
        session.add(TeamMember(team_id=team.id,user_id=person_id,access_role="admin"));await session.commit()
    from src.api.routes.feature_access import update,PermissionUpdate
    async with factory() as session:
        person=await session.get(User,person_id)
        with pytest.raises(fastapi.HTTPException) as error:
            await update(person_id,PermissionUpdate(expected_version=0,permissions={},reason="Attempt privilege overwrite"),person,session)
        assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_concurrent_permission_edits_have_one_winner_and_one_audit_event(db):
    """Two administrators' stale screens must not silently overwrite each other."""
    import asyncio
    _, factory, person_id = db
    async with factory() as session:
        admin = User(email="race-admin@example.test",name="Admin",password_hash="test",is_admin=True)
        team = Team(name="Race Program")
        session.add_all([admin,team]); await session.flush()
        session.add(TeamMember(team_id=team.id,user_id=person_id,access_role="facilitator"))
        await session.commit(); admin_id=admin.id
    app=fastapi.FastAPI(); app.include_router(router)
    async def identity():
        async with factory() as session:
            return await session.get(User,admin_id)
    async def database():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_current_admin_user]=identity
    app.dependency_overrides[get_async_session]=database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        bodies=[{"expected_version":0,"permissions":p,"reason":"Concurrent permission acceptance"}
                for p in ({"course_workspace":["read"]},{})]
        responses=await asyncio.gather(*(client.put(f"/v2/feature-access/people/{person_id}",json=b) for b in bodies))
        assert sorted(r.status_code for r in responses)==[200,409]
        winner=next(r.json() for r in responses if r.status_code==200)
        async with factory() as session:
            person=await session.get(User,person_id)
            assert person.feature_permissions_version==1
            assert person.feature_permissions==winner['permissions']
            events=(await session.execute(sa.select(PracticeDashboardAuditEvent).where(
                PracticeDashboardAuditEvent.action=='personal_feature_access_updated',
                PracticeDashboardAuditEvent.target_id==str(person_id)))).scalars().all()
            assert len(events)==1
