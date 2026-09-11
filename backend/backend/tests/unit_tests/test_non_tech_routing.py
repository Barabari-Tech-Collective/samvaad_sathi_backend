import pytest

from src.services.syllabus_service import RoleManager
from src.services.full_stack_interview_questions import get_full_stack_questions


def test_derive_role_rejects_non_tech_track():
    manager = RoleManager()
    with pytest.raises(ValueError):
        manager.derive_role("Non-Tech: Hr And Communication")


def test_derive_role_still_works_for_tech_tracks():
    manager = RoleManager()
    # Should not raise for an ordinary tech track.
    role = manager.derive_role("frontend")
    assert isinstance(role, str) and role


def test_get_full_stack_questions_rejects_non_tech_domain():
    with pytest.raises(ValueError):
        get_full_stack_questions(
            domain="Non-Tech: Hr And Communication",
            years_experience=1.0,
            difficulty="easy",
        )
