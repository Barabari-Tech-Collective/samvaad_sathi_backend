"""Templates meant for particular courses.

A template with no courses is open to every learner, exactly as before. Once creators choose
courses, only learners enrolled in one of them see it or can start it. Staff are never filtered
here: creators and reviewers must still see every template to work on it.

The learner's courses are the ones on their account (chosen by them, or given by an
administrator), and only while that enrollment is current."""

from typing import Iterable

import fastapi
import sqlalchemy as sa

from src.models.db.assessment_assignment import AssessmentAssignment
from src.models.db.job_profile import JobProfile
from src.models.db.user import User
from src.services.people import student_only
from src.services.sampark_enrollment_sync import fresh

NOT_YOUR_COURSE = (
    "This interview is for learners in a different course. "
    "Check your courses under Profile, or ask your program team."
)


def template_courses(template) -> set[str]:
    ids = getattr(template, "course_ids", None)
    if not isinstance(ids, list):
        return set()
    return {str(x) for x in ids if isinstance(x, (str, int)) and str(x).strip()}


def learner_courses(user) -> set[str]:
    """Courses this learner currently counts as enrolled in."""
    if not fresh(user):
        return set()
    ids = getattr(user, "enrolled_course_ids", None)
    return {str(x) for x in ids if isinstance(x, (str, int))} if isinstance(ids, list) else set()


def open_to(template, courses: Iterable[str]) -> bool:
    """True when the template is for everyone, or shares a course with `courses`."""
    wanted = template_courses(template)
    return not wanted or bool(wanted & set(courses))


async def is_learner(session, user) -> bool:
    """A learner is anyone who is not staff. Staff are exempt from course filtering."""
    row = await session.execute(sa.select(User.id).where(User.id == user.id, student_only()))
    return row.first() is not None


ACTIVE_ASSIGNMENT = ("assigned", "started")


async def assigned_template_ids(session, student_id: int) -> list[int]:
    """Templates staff have explicitly given this learner. An assignment beats a course filter,
    so a learner is never shown an assignment they cannot open."""
    rows = await session.execute(
        sa.select(AssessmentAssignment.job_profile_id).where(
            AssessmentAssignment.student_id == student_id,
            AssessmentAssignment.status.in_(ACTIVE_ASSIGNMENT),
        )
    )
    return sorted({r[0] for r in rows.all()})


def visible_to_courses(courses: Iterable[str], also_ids: Iterable[int] = ()):
    """SQL: templates open to everyone, sharing a course with `courses`, or assigned to the learner."""
    mine = sorted({str(c) for c in courses})
    extra = sorted({int(i) for i in also_ids})
    # "No courses" is SQL NULL, a JSON null, or an empty list. The CASE keeps the length check
    # from ever running on a value that is not a list.
    empty_list = sa.case(
        (sa.func.jsonb_typeof(JobProfile.course_ids) == "array", sa.func.jsonb_array_length(JobProfile.course_ids) == 0),
        else_=sa.true(),
    )
    everyone = sa.or_(JobProfile.course_ids.is_(None), empty_list)
    clauses = [everyone]
    if mine:
        clauses.append(JobProfile.course_ids.has_any(sa.cast(mine, sa.ARRAY(sa.Text))))
    if extra:
        clauses.append(JobProfile.id.in_(extra))
    return sa.or_(*clauses)


async def require_start_allowed(session, user, template, course_id: str | None = None) -> None:
    """Refuse a learner starting a template meant for other courses."""
    wanted = template_courses(template)
    if not wanted or not await is_learner(session, user):
        return
    if template.id in await assigned_template_ids(session, user.id):
        return  # staff gave it to this learner on purpose
    if not open_to(template, learner_courses(user)):
        raise fastapi.HTTPException(403, NOT_YOUR_COURSE)
    if course_id is not None and str(course_id) not in wanted:
        raise fastapi.HTTPException(
            422, "This interview is not part of that course. Start it from one of its courses."
        )


def require_assignable(template, course_id: str) -> None:
    """Staff may only hand a template to a course it is meant for."""
    wanted = template_courses(template)
    if wanted and str(course_id) not in wanted:
        raise fastapi.HTTPException(
            422,
            "This template is meant for other courses, so it cannot be assigned in this one. "
            "Add the course to the template first.",
        )


async def course_options(session) -> list[dict]:
    """The courses a template can be meant for: those learners can be in.

    That is every course with a batch open for choosing, plus any course a learner has chosen
    before (so a course whose batches were later closed is still listed). Sampark being down
    only drops the first part."""
    from src.models.db.student_placement import StudentPlacement
    from src.services import student_placement

    found: dict[str, str | None] = {}
    rows = await session.execute(
        sa.select(StudentPlacement.course_id, StudentPlacement.course_name)
        .distinct()
        .order_by(StudentPlacement.course_id)
    )
    for course_id, name in rows.all():
        found[course_id] = name
    try:
        open_now, _stale = await student_placement.open_batches()
    except fastapi.HTTPException:
        open_now = []
    for batch in open_now:
        found[batch.course] = batch.courseName or found.get(batch.course)
    return sorted(
        ({"course_id": cid, "course_name": name or f"Course {cid}"} for cid, name in found.items()),
        key=lambda item: item["course_name"].casefold(),
    )
