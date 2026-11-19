"""Which Samvaad staff sections a person may open (ADR-2: the backend decides access).

`/me` sends the result as `workspace_sections`, and the dashboard menu, page guard and home
task list use it instead of re-deriving access from role names. The rules are the ones the
dashboard used before this moved here; tests/unit_tests/test_role_matrix.py pins them for
every staff role row, and the dashboard checks the same fixture.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

# Sections in menu order: (id, the section its page resolves to, feature it needs,
# whether it ignores course scope). Generated from the dashboard's workspace map.
SECTIONS: list[tuple[str, str, str | None, bool]] = [
    ("create", "create", None, False),
    ("assessments", "assessments", "interview_authoring", False),
    ("human", "human", "human_interviews", False),
    ("readiness", "readiness", None, False),
    ("ai-assign", "ai-assign", "assessment_assignments", False),
    ("practice-sets", "practice-sets", "analytics", False),
    ("mock-attempts", "interviews", None, False),
    ("interviews", "interviews", None, False),
    ("results", "results", "analytics", True),
    ("reviews", "reviews", "ai_evaluation", False),
    ("coaching", "coaching", "practice_coaching", True),
    ("learning-plans", "learning-plans", "practice_coaching", False),
    ("courses", "courses", "course_workspace", False),
    ("cohorts", "cohorts", "course_workspace", False),
    ("students", "students", None, False),
    ("rankings", "rankings", "analytics", True),
    ("colleges", "colleges", "analytics", True),
    ("overview", "overview", "analytics", True),
    ("openings", "openings", "placement_shortlists", False),
    ("shortlists", "shortlists", "placement_shortlists", False),
    ("resumes", "resumes", "bulk_ats", False),
    ("question-banks", "practice-sets", "analytics", False),
    ("issues", "issues", "interview_authoring", False),
    ("alerts", "alerts", "analytics", True),
    ("people", "people", None, False),
    ("teams", "teams", "teams", False),
    ("course-batches", "course-batches", None, False),
    ("accommodations", "accommodations", "accommodations", False),
]

OPT_IN_FEATURES = {
    "human_interviews", "integrity_review", "ai_evaluation", "graduation",
    "placement_shortlists", "assessment_assignments", "accommodations",
}

Flags = Mapping[str, bool]


def _can_author(a: Flags) -> bool:
    return any(a[k] for k in ("can_access_interviews", "can_create_roles", "is_creator",
                              "is_sr_facilitator", "is_team_lead", "is_jr_facilitator"))


def _always(_a: Flags) -> bool:
    return True


def _never(_a: Flags) -> bool:
    return False


RULES: dict[str, Callable[[Flags], bool]] = {
    "create": lambda a: _can_author(a) or a["can_access_interviews"],
    "assessments": _can_author,
    "human": _always,
    "readiness": _always,
    "ai-assign": _always,
    "practice-sets": _can_author,
    "mock-attempts": lambda a: not a["is_creator"] and (
        a["can_access_interviews"] or a["is_team_lead"] or a["is_sr_facilitator"]),
    "interviews": _always,
    "results": _can_author,
    "reviews": _always,
    "coaching": lambda a: not a["is_creator"] and a["can_access_practice_interview"],
    "learning-plans": lambda a: not a["is_creator"] and a["can_access_practice_interview"],
    "courses": _always,
    "cohorts": _always,
    "students": lambda a: a["can_access_students"] or a["is_team_lead"] or a["is_sr_facilitator"] or a["is_jr_facilitator"],
    "rankings": lambda a: a["can_access_students"] or a["is_team_lead"],
    "colleges": lambda a: a["can_access_colleges"] or a["is_team_lead"] or a["is_sr_facilitator"] or a["is_jr_facilitator"],
    "overview": lambda a: a["can_access_dashboard"] or a["is_team_lead"] or a["is_sr_facilitator"] or a["is_jr_facilitator"],
    "openings": _always,
    "shortlists": _always,
    "resumes": lambda a: a["can_access_ats"] or a["can_use_bulk_ats"],
    "question-banks": _can_author,
    "issues": _can_author,
    "alerts": lambda a: a["is_admin"] or (a["is_team_lead"] and not a["is_creator"]),
    "people": _never,
    "teams": lambda a: not a["is_jr_facilitator"] and not a["is_creator"] and not a["is_team_member"] and a["is_team_lead"],
    "course-batches": _never,
    "accommodations": _always,
}


def _role_name(value) -> str:
    return str(value or "").strip().replace("_", " ").lower()


def workspace_flags(me: Mapping) -> dict[str, bool]:
    """The staff flags the menu rules read, from the /me payload the backend builds."""
    is_super_admin = bool(me.get("is_super_admin"))
    is_admin = bool(me.get("is_admin")) or is_super_admin
    is_team_lead = bool(me.get("is_team_lead"))
    memberships = [_role_name(r) for r in (me.get("membership_roles") or []) if _role_name(r)]
    access = _role_name(me.get("access_role"))
    if memberships:
        is_creator = any("creator" in r for r in memberships)
        is_sr = any(r in {"sr facilitator", "senior facilitator"} for r in memberships)
        is_jr = not is_team_lead and all(r in {"jr facilitator", "junior facilitator"} for r in memberships)
        is_member = all(r in {"team member", "member", "staff"} for r in memberships)
    else:
        is_creator = "creator" in access
        is_sr = access in {"sr facilitator", "senior facilitator"}
        is_jr = access in {"jr facilitator", "junior facilitator"}
        is_member = access in {"team member", "member"}
    flags = {
        "is_admin": is_admin, "is_super_admin": is_super_admin, "is_team_lead": is_team_lead,
        "is_creator": is_creator, "is_sr_facilitator": is_sr, "is_jr_facilitator": is_jr,
        "is_team_member": is_member,
        "course_limited": bool(me.get("course_limited")),
    }
    for key in ("can_create_roles", "can_access_dashboard", "can_access_students", "can_access_colleges",
                "can_access_interviews", "can_access_practice_interview", "can_access_ats", "can_use_bulk_ats"):
        flags[key] = bool(me.get(key))
    return flags


def _feature_open(policy, feature: str, administrator: bool) -> bool:
    # No per-person policy: /feature-access/me reports every feature for staff today.
    if policy is None or administrator:
        return True
    return "read" in (policy.get(feature) or [])


def workspace_sections(me: Mapping, policy: Mapping | None) -> list[str]:
    """Section ids, in menu order, this person may open."""
    a = workspace_flags(me)
    admin = a["is_admin"] or a["is_super_admin"]
    out = []
    for section_id, resolved, feature, course_blind in SECTIONS:
        if feature and not _feature_open(policy, feature, admin):
            continue
        if not admin:
            if course_blind and a["course_limited"]:
                continue
            if not RULES[resolved](a):
                continue
        out.append(section_id)
    return out
