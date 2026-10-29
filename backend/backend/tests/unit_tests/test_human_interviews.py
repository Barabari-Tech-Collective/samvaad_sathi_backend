import datetime as dt
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from src.api.routes.human_interviews import Create, Close
from src.services import human_interviews as service
from uuid import uuid4


@pytest.mark.parametrize(
    "value",
    [
        "http://meet.google.com/abc-defg-hij",
        "https://meet.google.com.evil.test/abc-defg-hij",
        "https://user@meet.google.com/abc-defg-hij",
        "https://meet.google.com:443/abc-defg-hij",
        "https://meet.google.com/abc-defg-hij?authuser=0",
        "https://meet.google.com/abc-defg-hij#x",
        "https://meet.google.com/%61bc-defg-hij",
        "javascript:alert(1)",
        "https://meet.google.com/lookup/name",
        "https://meet.google.com/abc-defg-hij\nhttps://evil.test",
        None,
    ],
)
def test_unsafe_or_nonstandard_links_are_rejected(value):
    with pytest.raises(ValueError):
        service.meet_url(value)


def test_url_and_timezone_contract():
    assert (
        service.meet_url(" https://meet.google.com/abc-defg-hij ")
        == "https://meet.google.com/abc-defg-hij"
    )
    with pytest.raises(ValidationError):
        Create(
            request_key=uuid4(),
            student_id=1,
            title="Interview",
            meet_url="https://meet.google.com/abc-defg-hij",
            starts_at="2026-10-21T12:00:00",
            ends_at="2026-10-21T13:00:00Z",
        )
    with pytest.raises(ValidationError):
        Close(expected_version=1, status="completed", reason="          ")


def test_join_boundaries_and_closed_status():
    now = dt.datetime.now(service.UTC)
    row = SimpleNamespace(
        starts_at=now, ends_at=now + dt.timedelta(hours=1), status="scheduled"
    )
    assert service.joinable(row, now - dt.timedelta(minutes=15))
    assert not service.joinable(row, now - dt.timedelta(minutes=15, microseconds=1))
    assert not service.joinable(row, row.ends_at)
    for state in ["cancelled", "completed", "no_show", "technical_issue"]:
        row.status = state
        assert not service.joinable(row, now)


def _row(**overrides):
    now = dt.datetime.now(service.UTC)
    values = dict(
        id=1,
        course_id="c",
        student_id=2,
        host_id=3,
        title="Technical Round 1",
        starts_at=now,
        ends_at=now + dt.timedelta(hours=1),
        status="completed",
        version=2,
        role_name="Software Developer",
        instructions="Bring your project",
        interviewer_name="Asha",
        reschedule_count=1,
        result="next_round",
        feedback="Clear explanation of the project.",
        result_released_at=None,
        closed_at=now,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_unreleased_result_is_hidden_from_students_but_visible_to_staff():
    now = dt.datetime.now(service.UTC)
    student = service.metadata(_row(), now)
    assert student["result"] is None and student["feedback"] is None
    assert student["result_released"] is False
    staff = service.metadata(_row(), now, can_manage=True, staff=True)
    assert staff["result"] == "next_round"
    assert staff["can_record_result"] is True
    released = service.metadata(_row(result_released_at=now), now)
    assert released["result"] == "next_round"
    assert released["role_name"] == "Software Developer"


def test_reschedule_and_outcome_contracts():
    from src.api.routes.human_interviews import Outcome, Reschedule

    with pytest.raises(ValidationError):
        Reschedule(
            expected_version=1,
            starts_at="2026-11-05T09:00:00",
            ends_at="2026-11-05T10:00:00Z",
            reason="Interviewer unwell",
        )
    ok = Reschedule(
        expected_version=1,
        starts_at="2026-11-05T09:00:00Z",
        ends_at="2026-11-05T10:00:00Z",
        meet_url="",
        reason="Interviewer unwell",
    )
    assert ok.meet_url is None
    with pytest.raises(ValidationError):
        Outcome(expected_version=1, result="passed", feedback="Good communication.")
    assert Create(
        request_key=uuid4(),
        student_id=1,
        title="Round 1",
        meet_url="https://meet.google.com/abc-defg-hij",
        starts_at="2026-11-05T09:00:00Z",
        ends_at="2026-11-05T10:00:00Z",
        instructions="   ",
    ).instructions is None


def test_interview_emails_render_escaped_and_branded():
    from src.services.email.human_interview_emails import build

    start = dt.datetime(2026, 11, 5, 9, 0, tzinfo=dt.timezone.utc)
    data = {
        "student_name": "Asha <script>",
        "title": "Technical Round 1",
        "role_name": "Software Developer",
        "interviewer_name": "Ravi",
        "instructions": "Explain your project",
        "starts_at": start,
        "ends_at": start + dt.timedelta(minutes=45),
        "result": "next_round",
        "feedback": "Clear and confident.",
    }
    for event in ["scheduled", "rescheduled", "cancelled", "result"]:
        subject, html, text = build(event, data)
        assert "<script>" not in html and "&lt;script&gt;" in html
        assert "#1F285B" in html and "Samvaad Saathi" in text
    subject, html, text = build("scheduled", data)
    assert "2:30 PM IST" in html and "Thursday, 5 November 2026" in subject
    assert "Explain your project" in text
    assert "You move to the next round" in build("result", data)[2]


def test_email_is_skipped_without_credentials(monkeypatch):
    from src.services.email import sender

    monkeypatch.setattr(sender.settings, "SMTP_LOGIN", "")
    assert sender.send_email("a@b.c", "s", "<p>h</p>", "t") is False
