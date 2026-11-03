from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import fastapi
from pydantic import ValidationError
from src.services.course_reporting import csv_cell, RosterExport
from src.services.feature_access import route_operation, permits
from src.api.routes import course_workspace as routes
from src.models.db.user import User


@pytest.mark.parametrize(
    "value", ["=1+1", "  +1", "\t@SUM(A1)", "\x00=1+1", "\x01-1", "\r=1"]
)
def test_formula_cells_are_neutralized(value):
    assert csv_cell(value).startswith("'")


def test_plain_unicode_and_empty_cells():
    assert csv_cell("नमस्ते, Student") == "नमस्ते, Student"
    assert csv_cell(None) == ""


def test_export_operation_is_independent_from_read():
    assert (
        route_operation("POST", "/v2/course-workspace/courses/a/students/export")
        == "export"
    )
    assert not permits({"course_workspace": ["read"]}, "course_workspace", "export")
    assert permits(
        {"course_workspace": ["read", "export"]}, "course_workspace", "export"
    )
    with pytest.raises(ValidationError):
        RosterExport(reason=" " * 20)


@pytest.mark.asyncio
async def test_export_bound_is_not_silent_truncation():
    session = AsyncMock()
    session.add = MagicMock()
    result = MagicMock()
    result.all.return_value = [object()] * 1001
    session.execute.return_value = result
    with patch.object(
        routes, "_shared_student_predicate", AsyncMock(return_value=True)
    ):
        with pytest.raises(fastapi.HTTPException) as error:
            await routes.export_course_students(
                "course-a",
                RosterExport(reason="Weekly learning review"),
                User(id=1, is_admin=True),
                session,
            )
    assert error.value.status_code == 413
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_export_audit_prevents_file_response():
    session = AsyncMock()
    session.add = MagicMock()
    result = MagicMock()
    result.all.return_value = [
        SimpleNamespace(
            id=2, name="Student", university="College", completed_practice=1
        )
    ]
    session.execute.return_value = result
    session.commit.side_effect = RuntimeError("Audit unavailable")
    with patch.object(
        routes, "_shared_student_predicate", AsyncMock(return_value=True)
    ):
        with pytest.raises(RuntimeError, match="Audit unavailable"):
            await routes.export_course_students(
                "course-a",
                RosterExport(reason="Weekly learning review"),
                User(id=1, is_admin=True),
                session,
            )
