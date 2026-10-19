from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import fastapi
from src.services.interview_course_access import require_interview_course_access


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "course,status",
    [
        (None, "active"),
        ("", "active"),
        ("course-a", "completed"),
        ("course-a", "expired"),
    ],
)
async def test_legacy_and_retained_history_do_not_require_new_enrollment(
    course, status
):
    session = AsyncMock()
    await require_interview_course_access(
        session, SimpleNamespace(course_id=course, status=status)
    )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_student_fails_closed_and_query_refreshes_identity_map():
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result
    with pytest.raises(fastapi.HTTPException) as error:
        await require_interview_course_access(
            session, SimpleNamespace(course_id="course-a", status="active", user_id=1)
        )
    assert error.value.detail["code"] == "COURSE_ACCESS_REVOKED"
    assert session.execute.call_args.args[0].get_execution_options()[
        "populate_existing"
    ]
