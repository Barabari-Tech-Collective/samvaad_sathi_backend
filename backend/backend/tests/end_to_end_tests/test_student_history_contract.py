"""History metadata, privacy and pagination continuity on PostgreSQL."""

import datetime
from types import SimpleNamespace
import pytest
from .test_fixed_question_rounds import db, client, pytestmark  # noqa: F401
from src.models.db.interview import Interview
from src.models.db.summary_report import SummaryReport
from src.models.db.user import User
from src.repository.crud.interview import InterviewCRUDRepository
from src.api.routes.interviews import history_percentages


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 2, 20, 100])
@pytest.mark.parametrize("summary", [False, True])
async def test_history_cursor_never_omits_lookahead_and_excludes_other_owners(
    db, limit, summary
):
    _, factory, user_id = db
    async with factory() as session:
        other = User(
            name="Other", email="other-history@example.test", password_hash="test"
        )
        session.add(other)
        await session.flush()
        expected = []
        for index in range(43):
            row = Interview(
                user_id=other.id if index % 7 == 0 else user_id,
                track="Communication",
                status="completed",
                interview_mode="practice",
            )
            session.add(row)
            await session.flush()
            if row.user_id == user_id:
                expected.append(row.id)
        await session.commit()
    cursor = None
    observed = []
    async with factory() as session:
        repo = InterviewCRUDRepository(async_session=session)
        method = (
            repo.list_by_user_cursor_with_summary
            if summary
            else repo.list_by_user_cursor
        )
        for _ in range(100):
            rows, next_cursor = await method(
                user_id=user_id, limit=limit, cursor_id=cursor
            )
            ids = [row[0].id for row in rows]
            observed.extend(ids)
            if next_cursor is None:
                break
            assert next_cursor == ids[-1]
            assert cursor is None or next_cursor < cursor
            cursor = next_cursor
        else:
            pytest.fail("Pagination did not terminate")
    assert observed == sorted(expected, reverse=True)
    assert len(observed) == len(set(observed))


@pytest.mark.asyncio
async def test_history_http_metadata_and_invalid_scores_do_not_hide_sessions(
    client, db
):
    _, factory, user_id = db
    async with factory() as session:
        row = Interview(
            user_id=user_id,
            track="Sensitive & technical",
            status="paused",
            interview_mode="assessment",
            expires_at=datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc),
            question_plan={"private": "must-not-appear"},
        )
        session.add(row)
        await session.flush()
        session.add(
            SummaryReport(
                interview_id=row.id,
                report_json={
                    "scoreSummary": {
                        "knowledgeCompetence": {"percentage": 0},
                        "speechAndStructure": {"percentage": "bad"},
                    }
                },
            )
        )
        await session.commit()
    response = await client.get("/interviews")
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["interviewMode"] == "assessment" and item["status"] == "paused"
    assert item["expiresAt"].startswith("2030-01-01")
    assert item["summaryReportAvailable"] is True
    assert item["knowledgePercentage"] == 0 and item["speechFluencyPercentage"] is None
    assert "must-not-appear" not in response.text
