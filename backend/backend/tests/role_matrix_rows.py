"""The staff role rows of the access audit, built from real membership records.

Each row is one kind of staff account. `me_payload` returns what `/me` sends for it, using
the same code the endpoint uses, so the frontend can check its menu against the backend's
own answer (tests/fixtures/role_matrix.json, copied into the dashboard repo).
"""
from types import SimpleNamespace

from src.api.dependencies.admin import is_bulk_ats_user
from src.api.routes.users import _extract_primary_membership_and_claims

PROGRAM = "Program Team"
PLACEMENT = "Placement Team"

# name -> (user fields, [(access_role, team_name, purposes, extra member fields)])
ROWS = {
    "Super admin": ({"is_super_admin": True, "is_admin": True}, []),
    "Admin": ({"is_admin": True}, []),
    "Program team lead": ({}, [("Team Lead", PROGRAM, None, {})]),
    "Placement team lead": ({}, [("Team Lead", PLACEMENT, None, {})]),
    "Program sr facilitator": ({}, [("Sr Facilitator", PROGRAM, None, {"colleges": ["MIT"]})]),
    "Program jr facilitator": ({}, [("Jr Facilitator", PROGRAM, None, {"colleges": ["MIT"]})]),
    "Program creator": ({}, [("Creator", PROGRAM, None, {})]),
    "Placement creator": ({}, [("Creator", PLACEMENT, None, {})]),
    "Program team member": ({}, [("Team Member", PROGRAM, None, {})]),
    "Placement team member": ({}, [("Team Member", PLACEMENT, None, {})]),
    "Placement sr facilitator": ({}, [("Sr Facilitator", PLACEMENT, None, {"colleges": ["MIT"]})]),
    "Placement jr facilitator": ({}, [("Jr Facilitator", PLACEMENT, None, {"colleges": ["MIT"]})]),
    "Course-limited sr facilitator": (
        {},
        [("Sr Facilitator", PROGRAM, None, {"colleges": ["MIT"], "course_ids": ["course-1"]})],
    ),
    "Purpose-tagged placement member": (
        {},
        [("Team Member", PLACEMENT, ["Check student progress", "Bulk ATS"], {})],
    ),
}


def _user(fields):
    base = {"id": 1, "email": "", "name": "Staff", "role": "staff", "is_admin": False, "is_super_admin": False}
    return SimpleNamespace(**(base | fields))


def _memberships(specs):
    out = []
    for i, (role, team, purposes, extra) in enumerate(specs):
        team_ns = SimpleNamespace(id=40 + i, name=team, status="active")
        member = SimpleNamespace(
            access_role=role, purposes=purposes, domain=None, colleges=extra.get("colleges"),
            course_ids=extra.get("course_ids"), team=team_ns, team_id=team_ns.id, status="active",
        )
        out.append((member, team_ns))
    return out


def me_payload(name):
    user_fields, specs = ROWS[name]
    user = _user(user_fields)
    claims = _extract_primary_membership_and_claims(user, _memberships(specs))
    keys = [
        "is_admin", "is_super_admin", "access_role", "role", "is_team_lead", "purposes", "domain",
        "can_create_roles", "can_access_ats", "can_edit", "is_view_only", "can_access_dashboard",
        "can_access_students", "can_access_colleges", "can_access_interviews",
        "can_access_practice_interview", "course_limited", "team_name", "membership_roles",
    ]
    payload = {k: claims[k] for k in keys}
    payload["can_use_bulk_ats"] = is_bulk_ats_user(user)
    return payload
