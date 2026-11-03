"""The role list a student's app receives must carry what its catalogue check requires.

The student app validates GET /v2/job-profiles strictly and rejects the whole list when one
required field is missing, which shows every student "Interview roles could not be loaded".
It requires created_at; this API names the same field createdAt. These tests pin the fields
(and their types and limits) that check depends on, so the two cannot drift apart again.
"""

import datetime
import json
from types import SimpleNamespace as NS

from src.models.db.job_profile import JobProfileStatus
from src.models.schemas.job_profile import JobProfileListResponse, JobProfileResponse


def _question(level: int, i: int):
    return NS(level=level, question_text=f"q{i}", id=i)


def _published_role(role_id=7, **overrides):
    now = datetime.datetime(2026, 11, 3, 8, 12, 37, tzinfo=datetime.timezone.utc)
    fields = dict(
        id=role_id,
        job_name="Backend developer",
        job_description="Build and run APIs.",
        skills=["python"],
        experience_level="fresher",
        created_at=now,
        updated_at=now,
        status=JobProfileStatus.PUBLISHED,
        company_name=None,
        additional_context=None,
        category="General Role",
        employment_type=None,
        interview_mode="assessment",
        questions_per_interview=5,
        questions=[_question(1, i) for i in range(1, 12)] + [_question(2, i) for i in range(20, 28)],
        title="Backend developer",
        description="Build and run APIs.",
        easy_questions=11,
        medium_questions=8,
        hard_questions=0,
        expert_questions=0,
        total_questions=19,
        submitted_at=None,
        admin_comment=None,
        content_revision=1,
        authoring_mode="ai",
        generation_token=None,
        generation_state=None,
        parent_profile_id=None,
        published_at=now,
    )
    fields.update(overrides)
    return NS(**fields)


def _as_sent(*roles):
    """The JSON a student's browser receives."""
    items = [JobProfileResponse.model_validate(role) for role in roles]
    return json.loads(
        JobProfileListResponse(items=items, total=len(items)).model_dump_json(by_alias=True)
    )


def test_a_published_role_carries_every_field_the_student_catalogue_requires():
    body = _as_sent(_published_role())
    row = body["items"][0]

    assert isinstance(row["id"], int) and row["id"] >= 1
    assert isinstance(row["title"], str) and 0 < len(row["title"]) <= 200
    assert isinstance(row["description"], str) and 0 < len(row["description"]) <= 5000
    assert row["category"] is None or (isinstance(row["category"], str) and len(row["category"]) <= 100)
    assert row["interviewMode"] in ("practice", "assessment")
    assert row["availableDifficulties"] == ["easy", "medium"]
    assert row["questionsPerInterview"] == 5
    # The field whose absence rejected every role list.
    assert datetime.datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
    assert body["total"] >= len(body["items"])


def test_created_at_and_createdAt_are_the_same_instant():
    row = _as_sent(_published_role())["items"][0]
    assert row["created_at"] == row["createdAt"]


def test_every_role_in_a_list_carries_it():
    body = _as_sent(_published_role(1), _published_role(2), _published_role(3, interview_mode="practice"))
    assert [r["id"] for r in body["items"]] == [1, 2, 3]
    assert all("created_at" in r for r in body["items"])
