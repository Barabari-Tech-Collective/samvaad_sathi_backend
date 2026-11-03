"""Who counts as a student.

Staff sign in to the same product and often take interviews to try it. Every student list,
count and score average must leave them out, or staff appear as students and their test
interviews move the averages. Use these predicates instead of filtering by hand.
"""

import sqlalchemy as sa

from src.models.db.interview import Interview
from src.models.db.team_member import TeamMember
from src.models.db.user import User


def student_only():
    """WHERE clause on User: not an active team member, no staff role, not an admin."""
    staff = sa.exists(
        sa.select(TeamMember.id).where(
            TeamMember.user_id == User.id, TeamMember.status == "active"
        )
    )
    return sa.and_(
        ~staff,
        User.role.is_(None),
        User.is_admin.is_(False),
        User.is_super_admin.is_(False),
    )


def student_interview():
    """WHERE clause on Interview: taken by a student. Needs no join to User."""
    return Interview.user_id.in_(sa.select(User.id).where(student_only()))
