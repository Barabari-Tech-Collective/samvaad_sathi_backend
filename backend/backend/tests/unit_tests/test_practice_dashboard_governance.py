"""Gate 5 permission, consent, privacy and workflow invariants."""

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fastapi
import pytest

from src.api.routes.practice_dashboard import (
    AssignmentCreate,
    ConsentUpdate,
    ShortlistDecision,
    _consent_is_active,
    _feedback_summary,
    _score,
    resolve_dashboard_role,
)
from src.models.db.practice_governance import PracticeConsent
from src.models.db.user import User
from src.services.practice_feature_controls import require_practice_feature


def _user(*, role: str | None = None, admin: bool = False, super_admin: bool = False):
    return User(
        id=1,
        email="staff@example.org",
        name="Staff",
        password_hash="unused",
        role=role,
        is_admin=admin,
        is_super_admin=super_admin,
    )


def test_role_resolution_prefers_super_admin_over_other_grants():
    assert (
        resolve_dashboard_role(_user(admin=True, super_admin=True), []) == "super_admin"
    )


def test_placement_membership_is_not_misclassified_as_facilitator():
    member = SimpleNamespace(access_role="Creator")
    team = SimpleNamespace(name="Placement and Partnerships")
    assert resolve_dashboard_role(_user(), [(member, team)]) == "placements"


def test_program_facilitator_membership_gets_coaching_role():
    member = SimpleNamespace(access_role="Junior Facilitator")
    team = SimpleNamespace(name="Programme Team")
    assert resolve_dashboard_role(_user(), [(member, team)]) == "facilitator"


def test_student_has_no_dashboard_role():
    assert resolve_dashboard_role(_user(), []) is None


def test_withdrawn_or_expired_consent_fails_closed():
    now = datetime.datetime.now(datetime.timezone.utc)
    consent = PracticeConsent(
        user_id=20,
        purpose="opportunity_readiness",
        granted=True,
        policy_version="v1",
        expires_at=now - datetime.timedelta(seconds=1),
    )
    assert _consent_is_active(consent, now) is False
    consent.expires_at = now + datetime.timedelta(days=1)
    consent.withdrawn_at = now
    assert _consent_is_active(consent, now) is False


def test_human_actions_require_meaningful_reasons():
    with pytest.raises(ValueError):
        AssignmentCreate(module="pace", reason="too short")
    with pytest.raises(ValueError):
        ShortlistDecision(student_id=1, decision="exclude", reason="no")


def test_consent_cannot_invent_an_unknown_purpose():
    with pytest.raises(ValueError):
        ConsentUpdate(
            purpose="marketing",  # type: ignore[arg-type]
            granted=True,
            policy_version="v1",
        )


def test_report_helpers_return_derived_fields_not_raw_content():
    report = {
        "scoreSummary": {"overallScore": 78},
        "executiveSummary": "Clear examples with room to improve structure.",
        "transcript": "must never be returned by the dashboard",
        "audioUrl": "https://private.example/audio.wav",
    }
    assert _score(report) == 78
    assert _feedback_summary(report) == "Clear examples with room to improve structure."


@pytest.mark.asyncio
async def test_paused_feature_returns_recoverable_service_error():
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(enabled=False)
    with pytest.raises(fastapi.HTTPException) as exc:
        await require_practice_feature(session, "question_generation")
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "PRACTICE_FEATURE_PAUSED"


@pytest.mark.asyncio
async def test_unconfigured_feature_defaults_to_enabled():
    session = AsyncMock()
    session.get.return_value = None
    await require_practice_feature(session, "practice_new_starts")
