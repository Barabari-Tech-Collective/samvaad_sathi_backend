from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import fastapi, pytest, httpx
from pydantic import ValidationError
from src.api.dependencies.admin import (
    is_admin_user,
    is_super_admin_user,
    calculate_user_permissions,
    get_current_creator_user,
)
from src.api.routes.staff_access import RelationshipUpdate, update_relationship
from src.api.routes.course_workspace import assignment_version
from src.models.schemas.teams_v2 import TeamMemberCreate, TeamMemberUpdate
from src.services.central_identity_access import current_identity
from src.services.staff_roles import canonical_role, relationship_rows
from src.config.manager import settings


def person(admin=False):
    return SimpleNamespace(
        id=1,
        email="staff@example.test",
        role=None,
        is_admin=admin,
        is_super_admin=False,
    )


def member():
    return SimpleNamespace(
        id=9,
        user_id=2,
        team_id=4,
        access_role="junior facilitator",
        status="active",
        course_ids=["old"],
        colleges=["College A"],
        domain="program",
        purposes=[],
        updated_at=None,
    )


@pytest.mark.parametrize(
    "role", ["ADMIN", "Super Admin", "SUPER_ADMIN", "central admin"]
)
def test_membership_cannot_assign_administrator_authority(role):
    with pytest.raises(ValidationError):
        TeamMemberUpdate(access_role=role)
    with pytest.raises(ValidationError):
        TeamMemberCreate(access_role=role, name="Staff", email="staff@example.test")


@pytest.mark.parametrize("role", ["ADMIN", "SUPER_ADMIN"])
def test_local_role_claim_cannot_escalate(role):
    p = person()
    p._token_source = "local"
    p._token_role = role
    p._token_is_admin = True
    p._token_is_super_admin = True
    assert not is_admin_user(p) and not is_super_admin_user(p)


def test_explicit_local_admin_survives_local_token_guard():
    p = person(True)
    p._token_source = "local"
    assert is_admin_user(p)


def test_all_relationships_keep_course_college_pairs():
    a, b = member(), member()
    b.id = 10
    b.course_ids = ["design"]
    b.colleges = ["College B"]
    result = relationship_rows(
        [(a, SimpleNamespace(name="Program")), (b, SimpleNamespace(name="Placement"))]
    )
    assert result[0]["course_ids"] == ["old"] and result[0]["colleges"] == ["College A"]
    assert result[1]["course_ids"] == ["design"] and result[1]["colleges"] == [
        "College B"
    ]


@pytest.mark.asyncio
async def test_admin_updates_responsibility_and_scope_atomically_with_audit():
    m = member()
    session = AsyncMock()
    session.add = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = m
    session.execute.return_value = result
    body = RelationshipUpdate(
        expected_version=assignment_version(m),
        responsibility="Sr Facilitator",
        course_ids=["development"],
        colleges=["College A"],
        reason="Approved coaching responsibility",
    )
    response = await update_relationship(9, body, person(True), session)
    assert response["responsibility"] == "senior facilitator" and response[
        "course_ids"
    ] == ["development"]
    assert session.add.call_args.args[0].event_metadata["before"]["course_ids"] == [
        "old"
    ]
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_relationship_cannot_overwrite_new_scope():
    m = member()
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = m
    session.execute.return_value = result
    body = RelationshipUpdate(
        expected_version="0" * 64,
        responsibility="facilitator",
        course_ids=[],
        reason="Approved responsibility change",
    )
    with pytest.raises(fastapi.HTTPException) as error:
        await update_relationship(9, body, person(True), session)
    assert error.value.status_code == 409
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_ordinary_staff_cannot_update_relationships():
    session = AsyncMock()
    body = RelationshipUpdate(
        responsibility="member", course_ids=[], reason="Approved responsibility change"
    )
    with pytest.raises(fastapi.HTTPException):
        await update_relationship(9, body, person(), session)
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_personal_authoring_grant_works_without_turning_facilitator_into_admin():
    p = person()
    p.feature_permissions = {"interview_authoring": ["read", "create"]}
    membership = SimpleNamespace(access_role="facilitator", colleges=["College A"])
    with patch(
        "src.api.dependencies.admin.get_user_active_memberships",
        AsyncMock(return_value=[membership]),
    ):
        assert await get_current_creator_user(p, AsyncMock()) is p
    perms = calculate_user_permissions(p, [membership])
    assert perms["can_create_roles"] and not perms["can_edit"] and not is_admin_user(p)


@pytest.mark.parametrize("status", [403, 404, 500, 302])
@pytest.mark.asyncio
async def test_fresh_central_verification_denies_or_holds_on_failure(
    monkeypatch, status
):
    monkeypatch.setattr(settings, "CENTRAL_IDENTITY_CHECK_ENABLED", True)
    monkeypatch.setattr(settings, "PRODUCT_IDENTITY_API_KEY", "test-only")
    monkeypatch.setattr(settings, "AUTH_SERVICE_BASE_URL", "https://auth.example.test")
    with pytest.raises(fastapi.HTTPException):
        await current_identity(
            {"sub": "staff@example.test", "userUniqueId": "id-1", "role": "ADMIN"},
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, json={})
            ),
        )


@pytest.mark.asyncio
async def test_central_role_changes_replace_stale_token_role(monkeypatch):
    monkeypatch.setattr(settings, "CENTRAL_IDENTITY_CHECK_ENABLED", True)
    monkeypatch.setattr(settings, "PRODUCT_IDENTITY_API_KEY", "test-only")
    monkeypatch.setattr(settings, "AUTH_SERVICE_BASE_URL", "https://auth.example.test")

    def response(request):
        assert request.headers["X-Product-Identity-Key"] == "test-only"
        return httpx.Response(
            200,
            json={
                "data": {
                    "userUniqueId": "id-1",
                    "email": "staff@example.test",
                    "role": "FACILITATOR",
                    "active": True,
                    "productUniqueId": settings.SAMPARK_PRODUCT_UNIQUE_ID,
                }
            },
        )

    claims = await current_identity(
        {"sub": "staff@example.test", "userUniqueId": "id-1", "role": "ADMIN"},
        transport=httpx.MockTransport(response),
    )
    assert claims["role"] == "FACILITATOR"


@pytest.mark.asyncio
async def test_removed_creator_cannot_reenter_via_old_token_label():
    p = person()
    p._token_role = "CREATOR"
    p._token_source = "central_sso"
    with patch(
        "src.api.dependencies.admin.get_user_active_memberships",
        AsyncMock(return_value=[]),
    ):
        with pytest.raises(fastapi.HTTPException):
            await get_current_creator_user(p, AsyncMock())


@pytest.mark.asyncio
async def test_removed_recruiter_cannot_reenter_via_old_token_label():
    from src.api.dependencies.admin import get_current_bulk_ats_user

    p = person()
    p._token_role = "RECRUITER"
    p._token_source = "central_sso"
    with patch(
        "src.api.dependencies.admin.get_user_active_memberships",
        AsyncMock(return_value=[]),
    ):
        with pytest.raises(fastapi.HTTPException):
            await get_current_bulk_ats_user(p, AsyncMock())


@pytest.mark.asyncio
async def test_first_binding_cannot_inherit_unverified_legacy_admin_grant(monkeypatch):
    from src.api.dependencies.auth import get_current_user
    from fastapi.security import HTTPAuthorizationCredentials
    from src.utilities.exceptions.database import EntityDoesNotExist

    monkeypatch.setattr(settings, "CENTRAL_IDENTITY_CHECK_ENABLED", True)
    p = person(True)
    p.central_identity_id = None
    repo = SimpleNamespace(
        get_user_by_central_identity=AsyncMock(
            side_effect=EntityDoesNotExist("not bound")
        ),
        get_user_by_email=AsyncMock(return_value=p),
        bind_central_identity=AsyncMock(),
    )
    claims = {
        "sub": p.email,
        "userUniqueId": "new-central-owner",
        "role": "FACILITATOR",
    }
    with (
        patch("src.api.dependencies.auth.decode_sso_access_token", return_value=claims),
        patch(
            "src.services.central_identity_access.current_identity",
            AsyncMock(return_value=claims),
        ),
        patch(
            "src.api.dependencies.auth.is_access_revoked", AsyncMock(return_value=False)
        ),
    ):
        with pytest.raises(fastapi.HTTPException) as error:
            await get_current_user(
                HTTPAuthorizationCredentials(scheme="Bearer", credentials="test"), repo
            )
    assert error.value.status_code == 409
    repo.bind_central_identity.assert_not_awaited()
