"""Product responsibilities are not central identity or administrator authority."""

ROLE_CATALOGUE = {
    "lead": "Team lead",
    "creator": "Interview creator",
    "facilitator": "Facilitator",
    "senior facilitator": "Senior facilitator",
    "junior facilitator": "Junior facilitator",
    "program manager": "Program manager",
    "placement staff": "Placement staff",
    "recruiter": "Recruiter",
    "partnership staff": "Partnership staff",
    "member": "Team member",
}
ALIASES = {
    "team lead": "lead",
    "manager": "lead",
    "sr facilitator": "senior facilitator",
    "jr facilitator": "junior facilitator",
    "team member": "member",
    "staff": "member",
    "placement com": "placement staff",
    "partnership": "partnership staff",
}


def canonical_role(value: str) -> str:
    role = " ".join(value.strip().replace("_", " ").lower().split())
    role = ALIASES.get(role, role)
    if role not in ROLE_CATALOGUE:
        raise ValueError(
            "Choose a supported staff responsibility. Administrator authority is managed separately."
        )
    return role


def unpack_membership(item):
    if isinstance(item, (tuple, list)) or hasattr(item, "_mapping"):
        return item[0], item[1] if len(item) > 1 else None
    return item, None


def relationship_rows(memberships):
    rows = []
    for item in memberships:
        member, team = unpack_membership(item)
        rows.append(
            {
                "membership_id": member.id,
                "team_id": member.team_id,
                "team_name": getattr(team, "name", None),
                "responsibility": member.access_role,
                "course_ids": (
                    member.course_ids if isinstance(member.course_ids, list) else []
                ),
                "colleges": member.colleges,
                "status": member.status,
                "scope_state": (
                    "assigned"
                    if isinstance(member.course_ids, list) and member.course_ids
                    else "needs_assignment"
                ),
            }
        )
    return rows
