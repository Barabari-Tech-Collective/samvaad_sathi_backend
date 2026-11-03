from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import fastapi
import pytest
from sqlalchemy.dialects import postgresql

from src.api.routes import course_workspace as workspace
from src.models.db.user import User
from src.services.coaching_review import review_fields


@pytest.mark.parametrize(
    "score", [True, False, float("nan"), float("inf"), 10**400, -1, 101, "75", None]
)
def test_invalid_scores_never_escape(score):
    assert (
        review_fields({"overallScoreSummary": {"overallPercentage": score}})["score"]
        is None
    )


def test_review_is_bounded_and_whitelisted():
    result = review_fields(
        {
            "overallScoreSummary": {"overallPercentage": 0},
            "summary": "x" * 900,
            "transcript": "private",
            "audio": "secret",
        }
    )
    assert result == {"state": "available", "score": 0, "feedback": "x" * 500}
    assert review_fields(None) == {
        "state": "unavailable",
        "score": None,
        "feedback": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["student", "session", None])
async def test_review_scope_filters_and_audit(missing):
    session = AsyncMock()
    session.add = MagicMock()
    student, interview = MagicMock(), MagicMock()
    student.scalar_one_or_none.return_value = None if missing == "student" else 77
    interview.one_or_none.return_value = (
        None
        if missing == "session"
        else SimpleNamespace(report_json={"summary": "Keep practicing"})
    )
    session.execute.side_effect = [student, interview]
    response = fastapi.Response()
    with patch.object(
        workspace, "_shared_student_predicate", AsyncMock(return_value=User.id == 77)
    ) as authorize:
        if missing:
            with pytest.raises(fastapi.HTTPException) as error:
                await workspace.review_course_practice(
                    "course-a", 77, 9, response, User(id=1), session
                )
            assert error.value.status_code == 404
            session.commit.assert_not_awaited()
            session.add.assert_not_called()
            return
        result = await workspace.review_course_practice(
            "course-a", 77, 9, response, User(id=1), session
        )
    authorize.assert_awaited_once()
    assert response.headers["Cache-Control"] == "no-store"
    assert result["feedback"] == "Keep practicing"
    query = (
        session.execute.call_args_list[-1].args[0].compile(dialect=postgresql.dialect())
    )
    assert query.params["course_id_1"] == "course-a"
    assert query.params["user_id_1"] == 77
    assert query.params["id_1"] == 9
    assert query.params["interview_mode_1"] == "practice"
    assert query.params["status_1"] == "completed"
    audit = session.add.call_args.args[0]
    assert audit.actor_id == 1 and audit.event_metadata["course_id"] == "course-a"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoked_scope_stops_before_evidence_query():
    session = AsyncMock()
    with patch.object(
        workspace,
        "_shared_student_predicate",
        AsyncMock(side_effect=fastapi.HTTPException(403, "Revoked")),
    ):
        with pytest.raises(fastapi.HTTPException):
            await workspace.review_course_practice(
                "course-a", 77, 9, fastapi.Response(), User(id=1), session
            )
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_failure_prevents_returning_feedback():
    session = AsyncMock()
    session.add = MagicMock()
    student, interview = MagicMock(), MagicMock()
    student.scalar_one_or_none.return_value = 77
    interview.one_or_none.return_value = SimpleNamespace(
        report_json={"summary": "Private feedback"}
    )
    session.execute.side_effect = [student, interview]
    session.commit.side_effect = RuntimeError("Audit unavailable")
    with patch.object(
        workspace, "_shared_student_predicate", AsyncMock(return_value=User.id == 77)
    ):
        with pytest.raises(RuntimeError, match="Audit unavailable"):
            await workspace.review_course_practice(
                "course-a", 77, 9, fastapi.Response(), User(id=1), session
            )
