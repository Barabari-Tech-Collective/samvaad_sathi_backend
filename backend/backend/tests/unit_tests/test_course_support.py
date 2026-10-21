from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import fastapi
from sqlalchemy.dialects import postgresql
from src.models.db.user import User
from src.api.routes.course_workspace import course_support
from src.services.course_support import support_query, allowed


def test_support_permission_is_explicit():
    for policy in [None, {}, {"course_workspace": ["read"]}]:
        assert not allowed(
            User(id=1, is_admin=False, is_super_admin=False, feature_permissions=policy)
        )
    assert allowed(
        User(
            id=1,
            is_admin=False,
            is_super_admin=False,
            feature_permissions={"practice_support": ["read"]},
        )
    )


def test_query_counts_failed_answers_without_loading_content():
    query = (
        support_query("course-a", User.id > 0)
        .select()
        .compile(dialect=postgresql.dialect())
    )
    assert "transcription_status" in str(query)
    assert "audio_url" not in str(query) and "transcription_text" not in str(query)
    assert "course-a" in query.params.values()
    assert "failed" in query.params.values()


@pytest.mark.asyncio
async def test_unassigned_support_access_stops_before_student_read():
    session = AsyncMock()
    with pytest.raises(fastapi.HTTPException) as error:
        await course_support(
            "course-a",
            fastapi.Response(),
            1,
            25,
            "all",
            User(id=1, is_admin=False, is_super_admin=False),
            session,
        )
    assert error.value.status_code == 403
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_failure_prevents_support_response():
    session = AsyncMock()
    session.add = MagicMock()
    summary, rows = MagicMock(), MagicMock()
    summary.one.return_value = MagicMock(sessions=0, failed_answers=0)
    rows.mappings.return_value.all.return_value = []
    session.execute.side_effect = [summary, rows]
    session.commit.side_effect = RuntimeError("Audit unavailable")
    with patch(
        "src.api.routes.course_workspace._shared_student_predicate",
        AsyncMock(return_value=True),
    ):
        with pytest.raises(RuntimeError, match="Audit unavailable"):
            await course_support(
                "course-a",
                fastapi.Response(),
                1,
                25,
                "all",
                User(id=1, is_admin=True),
                session,
            )
