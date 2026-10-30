"""Staff must never be counted as students, and their test interviews never move averages."""

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.db.interview import Interview
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.repository.table import Base
from src.services.people import student_interview, student_only

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


async def test_only_students_are_counted(factory):
    async with factory() as session:
        def person(email, **kw):
            return User(email=email, name=email.split("@")[0], password_hash="x", **kw)

        student = person("student@example.test")
        admin = person("admin@example.test", is_admin=True)
        super_admin = person("root@example.test", is_super_admin=True)
        legacy_staff = person("lead@example.test", role="team_lead")
        member = person("facilitator@example.test")
        former = person("left@example.test")
        session.add_all([student, admin, super_admin, legacy_staff, member, former])
        team = Team(name="Program")
        session.add(team)
        await session.flush()
        session.add_all([
            TeamMember(team_id=team.id, user_id=member.id, access_role="facilitator"),
            # Someone who left the team is a student again.
            TeamMember(team_id=team.id, user_id=former.id, access_role="facilitator", status="inactive"),
        ])
        session.add_all([
            Interview(user_id=uid, track="Data", difficulty="easy", status="completed")
            for uid in (student.id, admin.id, member.id)
        ])
        await session.commit()

        names = set((await session.execute(sa.select(User.name).where(student_only()))).scalars())
        assert names == {"student", "left"}

        owners = set((await session.execute(sa.select(Interview.user_id).where(student_interview()))).scalars())
        assert owners == {student.id}
