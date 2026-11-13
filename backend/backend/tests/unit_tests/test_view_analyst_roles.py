"""Five roles read analytics without editing anything (approved 2026-11-13)."""

import types

import pytest

from src.api.dependencies.admin import calculate_user_permissions
from src.services.course_access import ANALYTICS_AREAS


def _user():
    return types.SimpleNamespace(
        email="x@example.com", role=None, is_admin=False, is_super_admin=False,
        _token_source=None, _token_role=None, feature_permissions=None,
    )


def _perms(role, team="Program", **member_fields):
    team_obj = types.SimpleNamespace(name=team)
    member = types.SimpleNamespace(
        access_role=role, purposes=None, domain=None, course_ids=[], colleges=None, team=team_obj,
        **member_fields,
    )
    return calculate_user_permissions(_user(), [(member, team_obj)])


@pytest.mark.parametrize("team", ["Program", "Placement"])
@pytest.mark.parametrize(
    "role",
    ["facilitator", "program manager", "placement staff", "recruiter", "partnership staff"],
)
def test_the_five_roles_read_every_analytics_area_but_change_nothing(role, team):
    perms = _perms(role, team)
    for flags in ANALYTICS_AREAS.values():
        if "can_create_roles" in flags:
            continue  # the roles area also needs the right to author, which these roles lack
        assert any(perms[f] for f in flags), (role, flags)
    assert perms["is_view_only"] is True and perms["can_edit"] is False
    assert perms["can_create_roles"] is False
    assert perms["can_access_practice_interview"] is False
    assert perms["is_team_lead"] is False
    assert perms["colleges"] is None


def test_roles_that_were_not_part_of_the_decision_are_unchanged():
    member = _perms("member")
    assert not any(member[f] for f in ("can_access_dashboard", "can_access_students", "can_access_colleges", "can_access_interviews"))
    junior = _perms("junior facilitator")
    assert junior["colleges"] == []  # a junior facilitator still sees only assigned colleges
