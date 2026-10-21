"""Course support signal classification and reauthorization on PostgreSQL."""

import datetime
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa

from .test_course_reporting import reporting  # noqa: F401
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401
from src.models.db.user import User
from src.models.db.interview import Interview
from src.models.db.summary_report import SummaryReport
from src.models.db.team_member import TeamMember
from src.models.db.practice_governance import (
    PracticeConsent,
    PracticeDashboardAuditEvent,
)


@pytest.fixture
async def support(reporting):
    client, factory, actor_id, ids = reporting
    now = datetime.datetime.now(datetime.timezone.utc)
    sessions = {}
    async with factory() as session:
        (await session.get(User, actor_id)).feature_permissions = {
            "course_workspace": ["read"],
            "practice_support": ["read"],
        }
        await session.execute(sa.delete(Interview))
        specs = [
            ("blocked", ids["alice"], "course-a", "practice", "failed", 0, False),
            ("delayed", ids["zero"], "course-a", "practice", "completed", 20, False),
            ("open", ids["zero"], "course-a", "practice", "paused", 150, False),
            ("recent", ids["zero"], "course-a", "practice", "completed", 5, False),
            ("reported", ids["alice"], "course-a", "practice", "completed", 25, True),
            (
                "wrong_college",
                ids["wrong_college"],
                "course-a",
                "practice",
                "failed",
                150,
                False,
            ),
            (
                "wrong_course",
                ids["alice"],
                "course-b",
                "practice",
                "failed",
                150,
                False,
            ),
            ("formal", ids["alice"], "course-a", "assessment", "failed", 150, False),
            ("legacy", ids["alice"], None, "practice", "failed", 150, False),
            (
                "no_consent",
                ids["no_consent"],
                "course-a",
                "practice",
                "failed",
                150,
                False,
            ),
            ("stale", ids["stale"], "course-a", "practice", "failed", 150, False),
        ]
        for key, student_id, course, mode, status, age, reported in specs:
            row = Interview(
                user_id=student_id,
                course_id=course,
                interview_mode=mode,
                status=status,
                track="Communication",
                created_at=now - datetime.timedelta(minutes=age),
                completed_at=(
                    now - datetime.timedelta(minutes=age)
                    if status == "completed"
                    else None
                ),
            )
            session.add(row)
            await session.flush()
            sessions[key] = row.id
            if reported:
                session.add(
                    SummaryReport(
                        interview_id=row.id, report_json={"summary": "Private feedback"}
                    )
                )
        await session.commit()
    yield client, factory, actor_id, ids, sessions


BASE = "/v2/course-workspace/courses/course-a/support"


@pytest.mark.asyncio
async def test_queue_counts_and_rows_share_scope_and_thresholds(support):
    client, factory, actor_id, _, sessions = support
    response = await client.get(BASE, params={"limit": 1, "page": 2})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 3 and len(data["items"]) == 1
    assert data["failed_answers"] == 0
    assert response.headers["cache-control"] == "no-store"
    all_rows = (await client.get(BASE)).json()["items"]
    assert {row["interview_id"] for row in all_rows} == {
        sessions["blocked"],
        sessions["delayed"],
        sessions["open"],
    }
    assert {row["issue"] for row in all_rows} == {
        "session_blocked",
        "report_delayed",
        "open_over_two_hours",
    }
    assert set(all_rows[0]) == {
        "interview_id",
        "student_id",
        "status",
        "created_at",
        "completed_at",
        "issue",
        "failed_answers",
    }
    filtered = (await client.get(BASE, params={"issue": "report_delayed"})).json()
    assert (
        filtered["total"] == 1
        and filtered["items"][0]["interview_id"] == sessions["delayed"]
    )
    assert (await client.get(BASE, params={"page": 90})).json()["items"] == []
    assert (await client.get(BASE, params={"issue": "cheating"})).status_code == 422
    async with factory() as session:
        events = (
            (
                await session.execute(
                    sa.select(PracticeDashboardAuditEvent).where(
                        PracticeDashboardAuditEvent.action == "course_support_viewed"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 4 and all(row.actor_id == actor_id for row in events)


@pytest.mark.asyncio
async def test_queue_revocation_does_not_keep_old_records_visible(support):
    client, factory, actor_id, ids, _ = support
    assert (await client.get(BASE)).json()["total"] == 3
    async with factory() as session:
        await session.execute(
            sa.update(PracticeConsent)
            .where(PracticeConsent.user_id == ids["zero"])
            .values(withdrawn_at=datetime.datetime.now(datetime.timezone.utc))
        )
        await session.commit()
    assert (await client.get(BASE)).json()["total"] == 1
    async with factory() as session:
        (await session.get(User, actor_id)).feature_permissions = {
            "course_workspace": ["read"]
        }
        await session.commit()
    assert (await client.get(BASE)).status_code == 403
    async with factory() as session:
        (await session.get(User, actor_id)).feature_permissions = {
            "course_workspace": ["read"],
            "practice_support": ["read"],
        }
        await session.execute(
            sa.update(TeamMember)
            .where(
                TeamMember.user_id == actor_id,
                TeamMember.course_ids.contains(["course-a"]),
            )
            .values(access_role="placement staff")
        )
        await session.commit()
    assert (await client.get(BASE)).status_code == 403


@pytest.mark.asyncio
async def test_answer_failure_counts_and_empty_reports_do_not_hide_support(support):
    from src.models.db.question_attempt import QuestionAttempt

    client, factory, _, _, sessions = support
    async with factory() as session:
        for key in ("blocked", "reported", "formal", "wrong_college"):
            session.add(
                QuestionAttempt(
                    interview_id=sessions[key],
                    question_text="private prompt",
                    transcription_status="failed",
                    transcription_error="private provider detail",
                )
            )
        session.add(SummaryReport(interview_id=sessions["delayed"], report_json=None))
        await session.commit()
    response = (await client.get(BASE)).json()
    assert response["total"] == 4 and response["failed_answers"] == 2
    failed = (
        await client.get(BASE, params={"issue": "answer_processing_failed"})
    ).json()
    assert failed["total"] == 1 and failed["failed_answers"] == 1
    assert failed["items"][0]["interview_id"] == sessions["reported"]
    assert "private" not in str(response)
    delayed = (await client.get(BASE, params={"issue": "report_delayed"})).json()
    assert delayed["total"] == 1
    async with factory() as session:
        await session.execute(
            sa.update(SummaryReport)
            .where(SummaryReport.interview_id == sessions["delayed"])
            .values(report_json={})
        )
        await session.commit()
    assert (await client.get(BASE, params={"issue": "report_delayed"})).json()[
        "total"
    ] == 1
