import datetime as dt
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from src.api.routes.integrity_review import StaffAction, LearnerAction
from src.services.integrity_review import transition, serialize

NOW = dt.datetime.now(dt.timezone.utc)


def case(**changes):
    return SimpleNamespace(
        id=1,
        interview_id=2,
        student_id=3,
        course_id="course-a",
        state="decided",
        version=2,
        outcome="concern",
        decided_by=4,
        opened_by=4,
        appeal_deadline=NOW + dt.timedelta(days=2),
        history=[
            {
                "kind": "opened",
                "reason": "Private third-party information requiring removal.",
                "at": NOW.isoformat(),
                "source": "interviewer_observation",
            }
        ],
        **changes,
    )


def test_redaction_keeps_private_original_and_extends_learner_appeal():
    row = case()
    admin = SimpleNamespace(id=5, is_admin=True, is_super_admin=False)
    body = StaffAction(
        request_key=uuid4(),
        expected_version=2,
        action="redact",
        target_index=0,
        reason="Third-party details removed. Relevant evidence is under review.",
        shared_evidence_confirmed=True,
    )
    assert transition(row, admin, body, NOW, staff=True)
    assert row.appeal_deadline == NOW + dt.timedelta(days=14)
    assert "Private third-party" in row.history[0]["reason"]
    public = serialize(row, SimpleNamespace(id=3), NOW)
    assert "Private third-party" not in str(public)
    assert public["history"][0]["redacted"]


def test_history_reserves_room_for_appeal_after_evidence_limit():
    row = case()
    row.history = [
        {"kind": "add_evidence", "reason": "Technical context", "at": NOW.isoformat()}
        for _ in range(30)
    ]
    body = LearnerAction(
        request_key=uuid4(),
        expected_version=2,
        action="appeal",
        reason="Please independently review this technical incident.",
    )
    assert transition(row, SimpleNamespace(id=3), body, NOW, staff=False)
    assert row.state == "appealed"


def test_learner_explanation_limit_and_exact_deadline():
    row = case()
    row.appeal_deadline = NOW
    body = LearnerAction(
        request_key=uuid4(),
        expected_version=2,
        action="appeal",
        reason="Please review the evidence independently.",
    )
    with pytest.raises(HTTPException):
        transition(row, SimpleNamespace(id=3), body, NOW, staff=False)
    row.state = "open"
    row.history = [
        {"kind": "explain", "at": NOW.isoformat(), "reason": "Prior context"}
        for _ in range(3)
    ]
    body = LearnerAction(
        request_key=uuid4(),
        expected_version=2,
        action="explain",
        reason="Additional context about my connection.",
    )
    with pytest.raises(HTTPException):
        transition(row, SimpleNamespace(id=3), body, NOW, staff=False)
