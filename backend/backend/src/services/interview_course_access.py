"""Revalidate course participation without deleting or scoring retained evidence."""

import fastapi
import sqlalchemy as sa
from src.models.db.user import User
from src.services.sampark_enrollment_sync import fresh


def access_error(code, message):
    return fastapi.HTTPException(403, {"code": code, "message": message})


async def require_interview_course_access(session, interview):
    course_id = getattr(interview, "course_id", None)
    # Untagged historical/self-practice sessions keep their original contract.
    # Retained history/receipts and already accepted processing are unaffected.
    if not isinstance(course_id, str) or not course_id:
        return
    if getattr(interview, "status", None) not in {
        "active",
        "paused",
        "in_progress",
        "preparing",
    }:
        return
    if session is None:
        raise fastapi.HTTPException(503, "Course access verification is temporarily unavailable. Saved evidence is retained.")
    student = (
        await session.execute(
            sa.select(User)
            .where(User.id == interview.user_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if student is None or course_id not in (student.enrolled_course_ids or []):
        raise access_error(
            "COURSE_ACCESS_REVOKED",
            "Course enrollment is no longer available for this interview. Saved answers are retained. Ask your program team to check enrollment before continuing.",
        )
    if not fresh(student):
        raise access_error(
            "COURSE_ACCESS_UNVERIFIED",
            "Current course enrollment could not be verified. Saved answers are retained. Ask your program team to refresh enrollment, then retry.",
        )
