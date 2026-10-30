"""The Opportunities list: every shortlist a person may read, across their courses, no more."""

import datetime as dt
import os

import fastapi
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api.routes.course_shortlists import all_lists
from src.models.db.practice_governance import PracticeShortlist, PracticeShortlistEntry
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.repository.table import Base

URL = os.environ.get("FIXED_ROUND_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not URL, reason="Requires an explicitly isolated local PostgreSQL test database"
)


@pytest.fixture
async def factory():
    target = sa.engine.make_url(URL)
    if target.host not in ("127.0.0.1", "localhost") or target.database != "ss_count_tests":
        raise RuntimeError("Refusing schema mutation outside the isolated ss_count_tests database")
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _names(session, user, **kw):
    out = await all_lists(fastapi.Response(), user=user, session=session, page=1, limit=25, status=None, **kw)
    return {i["name"] for i in out["items"]}, out


async def test_lists_only_courses_with_a_placement_grant(factory):
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)
    read = {"placement_shortlists": ["read"]}
    async with factory() as session:
        admin = User(email="admin@example.test", name="admin", password_hash="x", is_admin=True)
        placer = User(email="placer@example.test", name="placer", password_hash="x", feature_permissions=read)
        coach = User(email="coach@example.test", name="coach", password_hash="x", feature_permissions=read)
        no_perm = User(email="np@example.test", name="np", password_hash="x")
        student = User(email="s@example.test", name="s", password_hash="x")
        session.add_all([admin, placer, coach, no_perm, student])
        team = Team(name="Placement")
        session.add(team)
        await session.flush()
        session.add_all([
            TeamMember(team_id=team.id, user_id=placer.id, access_role="team member",
                       purposes=["placement"], course_ids=["c1"]),
            # Same course, but a facilitator responsibility: not a placement grant.
            TeamMember(team_id=team.id, user_id=coach.id, access_role="sr facilitator",
                       purposes=["facilitation"], course_ids=["c1"]),
        ])
        c1 = PracticeShortlist(course_id="c1", name="Infosys data roles", opportunity="Infosys",
                               evidence_rules={}, created_by=admin.id, expires_at=later)
        c2 = PracticeShortlist(course_id="c2", name="TCS support", opportunity="TCS",
                               evidence_rules={}, created_by=admin.id, expires_at=later)
        session.add_all([c1, c2])
        await session.flush()
        session.add(PracticeShortlistEntry(shortlist_id=c1.id, student_id=student.id, decision="include",
                                           reason="Strong recent assessments", evidence_snapshot={},
                                           decided_by=admin.id))
        await session.commit()

        names, _ = await _names(session, admin)
        assert names == {"Infosys data roles", "TCS support"}

        names, out = await _names(session, placer)
        assert names == {"Infosys data roles"}
        assert out["items"][0]["included"] == 1 and out["items"][0]["excluded"] == 0

        names, _ = await _names(session, coach)
        assert names == set()

        with pytest.raises(fastapi.HTTPException) as refused:
            await _names(session, no_perm)
        assert refused.value.status_code == 403
