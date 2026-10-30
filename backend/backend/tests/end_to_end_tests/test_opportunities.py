"""Opportunities slice 2: one opening with candidates, rounds and evidence, under the
existing placement access rules."""

import datetime as dt
import os
import uuid

import fastapi
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api.routes import opportunities as routes
from src.models.db.interview import Interview
from src.models.db.job_profile import JobProfile
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


def _student(email, course="c1"):
    return User(
        email=email, name=email.split("@")[0], password_hash="x",
        enrolled_course_ids=[course], enrollment_source="sampark",
        enrollment_observed_at=dt.datetime.now(dt.timezone.utc),
    )


async def test_opening_candidates_rounds_and_evidence(factory):
    now = dt.datetime.now(dt.timezone.utc)
    can = {"placement_shortlists": ["read", "create", "update"]}
    async with factory() as session:
        lead = User(email="lead@example.test", name="lead", password_hash="x", feature_permissions=can)
        viewer = User(email="view@example.test", name="view", password_hash="x",
                      feature_permissions={"placement_shortlists": ["read"]})
        ready = _student("ready@example.test")
        fresh = _student("new@example.test")
        elsewhere = _student("other@example.test", course="c2")
        session.add_all([lead, viewer, ready, fresh, elsewhere])
        team = Team(name="Placement")
        session.add(team)
        await session.flush()
        for person in (lead, viewer):
            session.add(TeamMember(team_id=team.id, user_id=person.id, access_role="team member",
                                   purposes=["placement"], course_ids=["c1"]))
        role = JobProfile(job_name="Data Analyst", job_description="d", status="published",
                          interview_mode="assessment")
        draft = JobProfile(job_name="Draft role", job_description="d", status="draft",
                           interview_mode="assessment")
        session.add_all([role, draft])
        await session.flush()
        session.add_all([
            Interview(user_id=ready.id, track="Data", difficulty="easy", status="completed",
                      interview_mode="assessment", course_id="c1", job_profile_id=role.id,
                      completed_at=now - dt.timedelta(days=5)),
            # Too old for a 30-day rule, and a mock: neither counts as evidence.
            Interview(user_id=fresh.id, track="Data", difficulty="easy", status="completed",
                      interview_mode="assessment", course_id="c1", completed_at=now - dt.timedelta(days=60)),
            Interview(user_id=fresh.id, track="Data", difficulty="easy", status="completed",
                      interview_mode="practice", course_id="c1", completed_at=now - dt.timedelta(days=1)),
        ])
        await session.commit()

        create = routes.Create(request_key=uuid.uuid4(), title="Associate Data Analyst",
                               organisation="Infosys", job_profile_id=role.id,
                               max_evidence_age_days=30, min_assessments=1)
        opening = await routes.create_opportunity("c1", create, fastapi.Response(), user=lead, session=session)
        # The same request again returns the same opening.
        again = await routes.create_opportunity("c1", create, fastapi.Response(), user=lead, session=session)
        assert again["id"] == opening["id"]

        # A course outside the placement responsibility is refused.
        with pytest.raises(fastapi.HTTPException) as other_course:
            await routes.create_opportunity(
                "c2", routes.Create(request_key=uuid.uuid4(), title="TCS", organisation="TCS"),
                fastapi.Response(), user=lead, session=session)
        assert other_course.value.status_code == 403
        # A read-only member cannot create.
        with pytest.raises(fastapi.HTTPException) as no_create:
            await routes.create_opportunity(
                "c1", routes.Create(request_key=uuid.uuid4(), title="X role", organisation="Xcorp"),
                fastapi.Response(), user=viewer, session=session)
        assert no_create.value.status_code == 403

        added = await routes.add_candidates(
            opening["id"], routes.Candidates(student_ids=[ready.id, fresh.id, elsewhere.id]),
            user=lead, session=session)
        assert added == {"added": sorted([ready.id, fresh.id]), "not_eligible": [elsewhere.id]}

        # An AI round needs a published assessment template.
        with pytest.raises(fastapi.HTTPException) as unpublished:
            await routes.add_round(opening["id"], routes.Round(kind="ai", name="AI", job_profile_id=draft.id),
                                   user=lead, session=session)
        assert unpublished.value.status_code == 422
        await routes.add_round(opening["id"], routes.Round(kind="ai", name="AI screen", job_profile_id=role.id),
                               user=lead, session=session)
        await routes.add_round(opening["id"], routes.Round(kind="human", name="Panel"),
                               user=lead, session=session)

        detail = await routes.opportunity_detail(opening["id"], fastapi.Response(), user=viewer, session=session)
        assert [r["name"] for r in detail["rounds"]] == ["AI screen", "Panel"]
        by_name = {c["name"]: c for c in detail["candidates"]}
        assert by_name["ready"]["state"] == "ready" and by_name["ready"]["assessments_for_role"] == 1
        # Old assessment and a mock interview do not count; "missing" is not "rejected".
        assert by_name["new"]["state"] == "missing" and by_name["new"]["assessments"] == 0
        assert by_name["new"]["shares_mock_data"] is False

        # Changing the evidence rules is a new eligibility version.
        changed = await routes.change_opportunity(opening["id"], routes.Change(min_assessments=2),
                                                  user=lead, session=session)
        assert changed["eligibility_version"] == 2
        unchanged = await routes.change_opportunity(opening["id"], routes.Change(title="Data Analyst I"),
                                                    user=lead, session=session)
        assert unchanged["eligibility_version"] == 2

        listing = await routes.list_opportunities(fastapi.Response(), status="open", user=viewer, session=session)
        assert listing["items"][0]["candidates"] == 2 and listing["items"][0]["rounds"] == 2
