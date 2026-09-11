import datetime
from types import SimpleNamespace

import pytest

from src.services.analytics import AnalyticsService


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class DummyDB:
    def __init__(self, users):
        self._users = users

    async def execute(self, _stmt):
        return _ScalarResult(self._users)


@pytest.mark.asyncio
async def test_system_analytics_uses_event_backed_funnel_and_report_engagement(monkeypatch):
    now = datetime.datetime.now(datetime.timezone.utc)
    users = [
        SimpleNamespace(id=1, target_position="Backend"),
        SimpleNamespace(id=2, target_position=None),
        SimpleNamespace(id=3, target_position=None),
    ]
    service = AnalyticsService(db=DummyDB(users))

    interviews = [
        SimpleNamespace(id=101, user_id=3, created_at=now, status="completed"),
    ]
    events = [
        SimpleNamespace(event_type="role_selected", user_id=1, event_data={}, created_at=now),
        SimpleNamespace(event_type="role_selected", user_id=2, event_data={}, created_at=now),
        SimpleNamespace(event_type="interview_started", user_id=1, event_data={}, created_at=now),
        SimpleNamespace(event_type="interview_started", user_id=2, event_data={}, created_at=now),
        SimpleNamespace(event_type="interview_completed", user_id=1, event_data={}, created_at=now),
        SimpleNamespace(event_type="report_viewed", user_id=1, event_data={}, created_at=now),
        SimpleNamespace(event_type="report_engagement", user_id=1, event_data={"time_spent_seconds": 30, "recommendation_clicks": 2}, created_at=now),
        SimpleNamespace(event_type="report_engagement", user_id=1, event_data={"time_spent_seconds": 10, "recommendation_clicks": 1}, created_at=now),
    ]

    async def fake_list_interviews_all(**_kwargs):
        return interviews

    async def fake_reports_by_interview(_interview_ids):
        return {}

    async def fake_summary_reports_by_interview(_interview_ids):
        return {}

    async def fake_list_events(**_kwargs):
        return events

    async def fake_users_with_practice():
        return {1}

    async def fake_practice_effectiveness():
        return {"users_with_measurable_practice_effect": 0}

    async def fake_retry_behavior():
        return {"avg_retries_before_completion": 0.0}

    async def fake_question_effectiveness(_interview_ids):
        return {"low_score_questions": [], "high_dropoff_questions": []}

    async def fake_global_improvement_percent():
        return 0.0

    monkeypatch.setattr(service, "_list_interviews_all", fake_list_interviews_all)
    monkeypatch.setattr(service, "_reports_by_interview", fake_reports_by_interview)
    monkeypatch.setattr(service, "_summary_reports_by_interview", fake_summary_reports_by_interview)
    monkeypatch.setattr(service, "_list_analytics_events", fake_list_events)
    monkeypatch.setattr(service, "_users_with_practice", fake_users_with_practice)
    monkeypatch.setattr(service, "_practice_effectiveness", fake_practice_effectiveness)
    monkeypatch.setattr(service, "_system_retry_behavior", fake_retry_behavior)
    monkeypatch.setattr(service, "_question_effectiveness", fake_question_effectiveness)
    monkeypatch.setattr(service, "_global_improvement_percent", fake_global_improvement_percent)

    result = await service.get_system_analytics()

    assert result["funnel"]["sign_up"] == 3
    assert result["funnel"]["select_role"] == 2
    assert result["funnel"]["start_interview"] == 2
    assert result["funnel"]["complete_interview"] == 1
    assert result["funnel"]["view_report"] == 1
    assert result["funnel"]["do_practice"] == 1

    assert result["report_usage"]["time_spent_on_report_seconds"] == 40
    assert result["report_usage"]["recommendation_clicks"] == 3
    assert result["report_usage"]["percent_users_open_report"] == 50.0


@pytest.mark.asyncio
async def test_system_analytics_falls_back_to_legacy_when_events_missing(monkeypatch):
    now = datetime.datetime.now(datetime.timezone.utc)
    users = [
        SimpleNamespace(id=1, target_position="Backend"),
        SimpleNamespace(id=2, target_position=None),
    ]
    service = AnalyticsService(db=DummyDB(users))

    interviews = [
        SimpleNamespace(id=101, user_id=1, created_at=now, status="completed"),
        SimpleNamespace(id=102, user_id=2, created_at=now, status="active"),
    ]
    reports = {
        101: SimpleNamespace(overall_score=80.0, speech_structure_fluency=None, knowledge_competence=None)
    }

    async def fake_list_interviews_all(**_kwargs):
        return interviews

    async def fake_reports_by_interview(_interview_ids):
        return reports

    async def fake_summary_reports_by_interview(_interview_ids):
        return {}

    async def fake_list_events(**_kwargs):
        return []

    async def fake_users_with_practice():
        return {2}

    async def fake_practice_effectiveness():
        return {}

    async def fake_retry_behavior():
        return {}

    async def fake_question_effectiveness(_interview_ids):
        return {"low_score_questions": [], "high_dropoff_questions": []}

    async def fake_global_improvement_percent():
        return 5.5

    monkeypatch.setattr(service, "_list_interviews_all", fake_list_interviews_all)
    monkeypatch.setattr(service, "_reports_by_interview", fake_reports_by_interview)
    monkeypatch.setattr(service, "_summary_reports_by_interview", fake_summary_reports_by_interview)
    monkeypatch.setattr(service, "_list_analytics_events", fake_list_events)
    monkeypatch.setattr(service, "_users_with_practice", fake_users_with_practice)
    monkeypatch.setattr(service, "_practice_effectiveness", fake_practice_effectiveness)
    monkeypatch.setattr(service, "_system_retry_behavior", fake_retry_behavior)
    monkeypatch.setattr(service, "_question_effectiveness", fake_question_effectiveness)
    monkeypatch.setattr(service, "_global_improvement_percent", fake_global_improvement_percent)

    result = await service.get_system_analytics()

    assert result["funnel"]["sign_up"] == 2
    assert result["funnel"]["select_role"] == 1
    assert result["funnel"]["start_interview"] == 2
    assert result["funnel"]["complete_interview"] == 1
    assert result["funnel"]["view_report"] == 1
    assert result["funnel"]["do_practice"] == 1

    assert result["report_usage"]["time_spent_on_report_seconds"] is None
    assert result["report_usage"]["recommendation_clicks"] is None
