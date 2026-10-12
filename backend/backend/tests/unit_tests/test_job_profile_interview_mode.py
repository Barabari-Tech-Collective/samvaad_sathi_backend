"""Practice and Assessment are a facilitator's choice at creation, nothing more.

A role's questions are the facilitator's in either mode - interview_mode exists
only to pick which student-facing entry point surfaces a role and whether a
completed sitting goes into the review/shortlist pipeline. It must never relax
question integrity, so interview_eligibility.py (minimum questions, published
status, no AI substitution) does not take this field as an input at all - the
same rules apply to a Practice role as to an Assessment one. This module pins
the schema-layer half of that: defaulting, validation, and that an update
omitting the field cannot silently flip a published role's mode.
"""

import ast
import datetime
import pathlib

import pydantic
import pytest

from src.models.schemas.job_profile import (
    JobProfileCreateV2,
    JobProfileResponse,
    JobProfileUpdateV2,
)

ELIGIBILITY_SOURCE = pathlib.Path(
    __file__
).resolve().parents[2] / "src" / "services" / "interview_eligibility.py"


def test_create_defaults_to_assessment():
    """Every role created before this field existed was, in effect, one."""
    profile = JobProfileCreateV2(jobName="Backend Engineer")

    assert profile.interview_mode == "assessment"


def test_create_accepts_practice():
    profile = JobProfileCreateV2(jobName="Backend Engineer", interviewMode="practice")

    assert profile.interview_mode == "practice"


def test_create_rejects_unknown_mode():
    """Not a free-text field - only the two the student app knows how to route."""
    with pytest.raises(pydantic.ValidationError):
        JobProfileCreateV2(jobName="Backend Engineer", interviewMode="scored")


def test_update_omitting_the_field_does_not_reset_it():
    """An update must be able to leave mode untouched.

    update_job_profile_review (and update_job_profile generally) pass
    model_dump(exclude_unset=True) to a generic setattr loop, so a None default
    here is what keeps an unrelated edit - job name, skills - from silently
    reverting a role's mode to the schema default.
    """
    update = JobProfileUpdateV2(jobName="Renamed Role")

    assert update.interview_mode is None
    assert "interview_mode" not in update.model_dump(exclude_unset=True)


def test_update_rejects_unknown_mode_when_provided():
    with pytest.raises(pydantic.ValidationError):
        JobProfileUpdateV2(interviewMode="scored")


def test_response_tolerates_a_none_value_on_the_underlying_object():
    """A row the DB default never touched must still read as assessment.

    Caught by test_submit_job_profile_admin_review_comment_only, which builds a
    JobProfile purely in memory and never flushes it - so interview_mode is
    None at the Python level even though the column can never be NULL in
    Postgres. Any such object - an unflushed row, a hand-built test double, a
    half-constructed object elsewhere - must resolve the same way the column's
    own server_default does, not 500.
    """
    now = datetime.datetime.now(datetime.timezone.utc)

    response = JobProfileResponse(
        job_name="Backend Engineer", created_at=now, status="published", id=1, interview_mode=None
    )

    assert response.interview_mode == "assessment"


def test_response_round_trips_through_the_orm_attribute_name():
    """JobProfileResponse reads the DB column name, not the wire name."""
    now = datetime.datetime.now(datetime.timezone.utc)
    response = JobProfileResponse(
        job_name="Backend Engineer",
        created_at=now,
        status="published",
        id=1,
        interview_mode="practice",
    )

    assert response.model_dump(by_alias=True)["interviewMode"] == "practice"


def test_eligibility_module_does_not_reference_interview_mode():
    """The structural half of the "never relaxes integrity" guarantee.

    interview_eligibility.py's functions take a job profile and compute
    availability from status and question counts alone. If this starts
    matching, someone made eligibility depend on Practice vs Assessment, which
    reopens exactly the question this field was built to avoid answering twice.
    """
    source = ELIGIBILITY_SOURCE.read_text()
    tree = ast.parse(source)

    referenced = any(
        (isinstance(node, ast.Name) and "interview_mode" in node.id)
        or (isinstance(node, ast.Attribute) and node.attr == "interview_mode")
        or (isinstance(node, ast.Constant) and node.value == "interview_mode")
        for node in ast.walk(tree)
    )

    assert not referenced, (
        "interview_eligibility.py now references interview_mode. Practice and "
        "Assessment must enforce identical question integrity - if there is a "
        "deliberate reason to branch on mode here, update this test's premise "
        "rather than silently letting it slip past review."
    )
