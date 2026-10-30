"""One learner record for staff (architecture pack sec 3, "One learner detail page").

The page has Summary, Coaching, Mock Interviews, Assessments and Opportunities sections.
Each section keeps its own endpoint and rules; this endpoint only says who the learner is,
which of the learner's courses this person may act in, which sections they may open, and
lists the cross-course records (human rounds, openings) that have no other per-learner view.
Mock and assessment data stay separate sections; analytics access is unchanged.
"""

import datetime as dt

import fastapi
import sqlalchemy as sa

from src.api.dependencies.admin import (
    get_current_admin_user,
    get_user_allowed_colleges,
    is_admin_user,
)
from src.api.dependencies.session import get_async_session
from src.models.db.human_interview import HumanInterview
from src.models.db.opportunity import Opportunity, OpportunityCandidate
from src.models.db.opportunity_selection import (
    OpportunitySelection,
    OpportunitySelectionEntry,
)
from src.models.db.user import User
from src.services import course_shortlists
from src.services import human_interviews as interviews
from src.services.course_access import grants_for, student_predicate
from src.services.feature_access import permits
from src.services.people import student_only

router = fastapi.APIRouter(prefix="/v2/learners", tags=["learners"])


def allowed(user, feature, operation="read"):
    return is_admin_user(user) or (
        isinstance(user.feature_permissions, dict)
        and permits(user.feature_permissions, feature, operation)
    )


async def visible_courses(user, student, session):
    """The learner's current courses that this person's course grants cover."""
    admin = is_admin_user(user)
    grants = () if admin else await grants_for(user, session)
    courses = []
    for course_id in sorted(set(student.enrolled_course_ids or [])):
        if not isinstance(course_id, str):
            continue
        inside = (
            await session.execute(
                sa.select(User.id).where(
                    User.id == student.id,
                    student_predicate(course_id, grants, global_access=admin),
                )
            )
        ).scalar_one_or_none()
        if inside is not None:
            courses.append(course_id)
    return courses


async def college_scope_allows(user, student, session):
    """The analytics rule: admins and unscoped staff see everyone; others their colleges."""
    colleges = await get_user_allowed_colleges(user, session)
    if colleges is None:
        return True
    college = (student.university or "").strip().lower()
    return college in {c.lower() for c in colleges}


async def human_rounds(user, student_id, courses, session):
    if not interviews.permitted(user, "read") or not courses:
        return None
    conditions = [
        HumanInterview.student_id == student_id,
        HumanInterview.course_id.in_(courses),
    ]
    # Same rule as the interviews list: only admins see rounds someone else hosts.
    if not is_admin_user(user):
        conditions.append(HumanInterview.host_id == user.id)
    rows = (
        await session.execute(
            sa.select(HumanInterview)
            .where(*conditions)
            .order_by(HumanInterview.starts_at.desc(), HumanInterview.id.desc())
            .limit(50)
        )
    ).scalars()
    return [
        {
            "id": row.id,
            "course_id": row.course_id,
            "opportunity_id": row.opportunity_id,
            "title": row.title,
            "round": row.role_name,
            "starts_at": row.starts_at.isoformat(),
            "status": row.status,
            "result": row.result,
            "result_released": row.result_released_at is not None,
        }
        for row in rows
    ]


async def openings(user, student_id, courses, session):
    if not course_shortlists.permission(user, "read"):
        return None
    placement = await course_shortlists.placement_course_ids(user, session)
    scope = courses if placement is None else [c for c in courses if c in placement]
    if not scope:
        return []
    latest = (
        sa.select(
            OpportunitySelection.opportunity_id,
            sa.func.max(OpportunitySelection.revision).label("revision"),
        )
        .group_by(OpportunitySelection.opportunity_id)
        .subquery()
    )
    selection = (
        sa.select(
            OpportunitySelection.opportunity_id,
            OpportunitySelection.status,
            OpportunitySelectionEntry.recommendation,
            OpportunitySelectionEntry.decision,
        )
        .join(
            latest,
            sa.and_(
                latest.c.opportunity_id == OpportunitySelection.opportunity_id,
                latest.c.revision == OpportunitySelection.revision,
            ),
        )
        .outerjoin(
            OpportunitySelectionEntry,
            sa.and_(
                OpportunitySelectionEntry.selection_id == OpportunitySelection.id,
                OpportunitySelectionEntry.student_id == student_id,
            ),
        )
        .subquery()
    )
    rows = (
        await session.execute(
            sa.select(
                Opportunity,
                OpportunityCandidate.added_at,
                selection.c.status,
                selection.c.recommendation,
                selection.c.decision,
            )
            .join(
                OpportunityCandidate,
                OpportunityCandidate.opportunity_id == Opportunity.id,
            )
            .outerjoin(selection, selection.c.opportunity_id == Opportunity.id)
            .where(
                OpportunityCandidate.student_id == student_id,
                Opportunity.course_id.in_(scope),
            )
            .order_by(Opportunity.id.desc())
        )
    ).all()
    return [
        {
            "id": o.id,
            "course_id": o.course_id,
            "title": o.title,
            "organisation": o.organisation,
            "status": o.status,
            "added_at": added.isoformat() if added else None,
            "selection_status": status,
            "recommendation": recommendation,
            "decision": decision,
        }
        for o, added, status, recommendation, decision in rows
    ]


@router.get("/{student_id}")
async def record(
    student_id: int,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    student = (
        await session.execute(
            sa.select(User).where(User.id == student_id, student_only())
        )
    ).scalar_one_or_none()
    if student is None:
        raise fastapi.HTTPException(404, "Learner not found within your access.")
    courses = await visible_courses(user, student, session)
    analytics = allowed(user, "analytics") and await college_scope_allows(
        user, student, session
    )
    # Someone with neither analytics reach nor a course covering this learner sees nothing,
    # not even the name.
    if not analytics and not courses:
        raise fastapi.HTTPException(404, "Learner not found within your access.")
    now = dt.datetime.now(dt.timezone.utc)
    return {
        "student": {
            "id": student.id,
            "name": student.name,
            "college": student.university,
            "degree": student.degree,
            "target_position": student.target_position,
        },
        "courses": courses,
        "sections": {
            "summary": analytics,
            # Every mock interview, as analytics shows them; course staff instead see what the
            # learner shared with the course, inside Coaching.
            "mock_interviews": analytics,
            "assessments": analytics or interviews.permitted(user, "read"),
            # Coaching follow-ups and shared practice for a course covering this learner.
            # The course endpoints still check the learner's sharing consent.
            "coaching": bool(courses),
            "opportunities": course_shortlists.permission(user, "read"),
        },
        "actions": {
            "assign_coaching": bool(courses)
            and allowed(user, "practice_coaching", "create"),
            "schedule_assessment": bool(courses) and interviews.permitted(user, "create"),
        },
        "human_rounds": await human_rounds(user, student.id, courses, session),
        "opportunities": await openings(user, student.id, courses, session),
        "as_of": now.isoformat(),
    }
