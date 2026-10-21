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
