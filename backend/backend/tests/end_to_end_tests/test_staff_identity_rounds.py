"""Disposable PostgreSQL checks for staff relationships and stable identity binding."""

from unittest.mock import patch, AsyncMock
from types import SimpleNamespace
import pytest, sqlalchemy as sa, fastapi
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt
from src.models.db.user import User
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.repository.crud.user import UserCRUDRepository
from src.utilities.exceptions.database import EntityAlreadyExists
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.admin import is_admin_user, is_super_admin_user
from src.api.routes.staff_access import RelationshipUpdate, update_relationship
from src.api.routes.course_workspace import assignment_version
from src.config.manager import settings
from src.securities.authorizations.sso_jwt import SsoTokenError
from .test_fixed_question_rounds import db, pytestmark  # noqa:F401


@pytest.mark.asyncio
async def test_stable_identity_binding_is_unique_and_independent_of_email(db):
    _, factory, user_id = db
    async with factory() as session:
        repo = UserCRUDRepository(async_session=session)
        user = await session.get(User, user_id)
        await repo.bind_central_identity(user, "central-one")
        assert (await repo.get_user_by_central_identity("central-one")).id == user_id
        user.email = "renamed@example.test"
        await session.commit()
        assert (await repo.get_user_by_central_identity("central-one")).id == user_id
        other = User(email="other@example.test", name="Other", password_hash="test")
        session.add(other)
        await session.commit()
        with pytest.raises(EntityAlreadyExists):
            await repo.bind_central_identity(other, "central-one")


@pytest.mark.asyncio
async def test_multi_team_scope_change_is_audited_and_does_not_change_other_membership(
    db,
):
    _, factory, user_id = db
    async with factory() as session:
        a, b = Team(name="Program"), Team(name="Placement")
        session.add_all([a, b])
        await session.flush()
        first = TeamMember(
            team_id=a.id,
            user_id=user_id,
            access_role="junior facilitator",
            colleges=["College A"],
            course_ids=["development"],
        )
        second = TeamMember(
            team_id=b.id,
            user_id=user_id,
            access_role="placement staff",
            colleges=["College B"],
            course_ids=["design"],
        )
        session.add_all([first, second])
        await session.commit()
        first_id, second_id = first.id, second.id
        body = RelationshipUpdate(
            expected_version=assignment_version(first),
            responsibility="senior facilitator",
            colleges=["College A"],
            course_ids=["development", "communication"],
            reason="Approved expanded coaching responsibility",
        )
        actor = SimpleNamespace(
            id=user_id, is_admin=True, is_super_admin=False, email="admin@example.test"
        )
        result = await update_relationship(first.id, body, actor, session)
        assert result["responsibility"] == "senior facilitator"
        with pytest.raises(fastapi.HTTPException) as error:
            await update_relationship(first_id, body, actor, session)
        assert error.value.status_code == 409
    async with factory() as session:
        untouched = await session.get(TeamMember, second_id)
        assert untouched.course_ids == ["design"] and untouched.colleges == [
            "College B"
        ]
        events = (
            (
                await session.execute(
                    sa.select(PracticeDashboardAuditEvent).where(
                        PracticeDashboardAuditEvent.action
                        == "staff_relationship_updated"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1 and events[0].event_metadata["before"][
            "course_ids"
        ] == ["development"]


@pytest.mark.asyncio
async def test_locally_signed_token_is_rejected_outright(db, monkeypatch):
    """Central auth is the only identity source: a token signed with the local secret,
    however privileged its claims, is a 401 and never reaches account lookup."""
    _, factory, user_id = db
    async with factory() as session:
        user = await session.get(User, user_id)
        token = jwt.encode(
            {"email": user.email, "role": "SUPER_ADMIN"},
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        with patch(
            "src.api.dependencies.auth.decode_sso_access_token",
            side_effect=SsoTokenError("local token"),
        ):
            with pytest.raises(fastapi.HTTPException) as rejected:
                await get_current_user(
                    HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
                    UserCRUDRepository(async_session=session),
                )
        assert rejected.value.status_code == 401


@pytest.mark.asyncio
async def test_multi_role_consent_stays_paired_with_membership_colleges(db):
    from src.api.routes.course_workspace import course_students
    from src.models.db.practice_governance import PracticeConsent

    _, factory, actor_id = db
    async with factory() as session:
        program, placement = Team(name="Program"), Team(name="Placement")
        a = User(
            email="a@example.test",
            name="Student A",
            password_hash="test",
            university="College A",
            enrolled_course_ids=["same-course"],
        )
        b = User(
            email="b@example.test",
            name="Student B",
            password_hash="test",
            university="College B",
            enrolled_course_ids=["same-course"],
        )
        session.add_all([program, placement, a, b])
        await session.flush()
        session.add_all(
            [
                TeamMember(
                    user_id=actor_id,
                    team_id=program.id,
                    access_role="facilitator",
                    course_ids=["same-course"],
                    colleges=["College A"],
                ),
                TeamMember(
                    user_id=actor_id,
                    team_id=placement.id,
                    access_role="placement staff",
                    course_ids=["same-course"],
                    colleges=["College B"],
                ),
            ]
        )
        consent_a = PracticeConsent(
            user_id=a.id,
            purpose="facilitator_coaching",
            granted=True,
            policy_version="test",
        )
        session.add_all(
            [
                consent_a,
                PracticeConsent(
                    user_id=b.id,
                    purpose="opportunity_readiness",
                    granted=True,
                    policy_version="test",
                ),
                PracticeConsent(
                    user_id=a.id,
                    purpose="opportunity_readiness",
                    granted=True,
                    policy_version="test",
                ),
            ]
        )
        await session.commit()
        actor = await session.get(User, actor_id)
        actor._token_role = "RECRUITER"
        actor._token_source = "central_sso"
        result = await course_students("same-course", 1, 25, actor, session)
        assert {item["id"] for item in result["items"]} == {a.id, b.id}
        # The Placement membership only covers College B; its consent purpose
        # cannot substitute for withdrawn coaching consent in College A.
        consent_a.granted = False
        await session.commit()
        result = await course_students("same-course", 1, 25, actor, session)
        assert {item["id"] for item in result["items"]} == {b.id}
