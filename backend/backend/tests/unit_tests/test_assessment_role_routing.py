"""Exercise the actual assessment authoring router, not a copied test route."""

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import fastapi
from fastapi.testclient import TestClient
import pytest

from src.api.dependencies.admin import get_current_creator_user
from src.api.routes.job_profiles_v2 import router
from src.repository.crud.job_profile import JobProfileCRUDRepository
from src.repository.crud.interview import InterviewCRUDRepository


def profile(mode="assessment"):
    return SimpleNamespace(
        id=41,
        job_name="Backend Engineer",
        job_description="Role description",
        interview_mode=mode,
        created_at=datetime.datetime.now(datetime.timezone.utc),
        status="draft",
        easy_questions=5,
        medium_questions=0,
        hard_questions=0,
        expert_questions=0,
        total_questions=5,
    )


@pytest.fixture
def authoring():
    app = fastapi.FastAPI()
    app.include_router(router)
    repo = MagicMock(spec=JobProfileCRUDRepository)
    repo.create_profile = AsyncMock(return_value=profile())
    repo.update_profile = AsyncMock(return_value=profile())
    repo.get_assessment_for_update = AsyncMock(return_value=profile())
    repo.submit_profile = AsyncMock()
    repo.update_review_status = AsyncMock()
    app.dependency_overrides[get_current_creator_user] = lambda: SimpleNamespace(
        id=7, is_admin=True
    )
    for route in router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == "job_profile_repo":
                app.dependency_overrides[dependency.call] = lambda: repo
    with TestClient(app) as client:
        yield client, repo


@pytest.mark.parametrize(
    "purpose", [{}, {"interviewMode": "assessment"}, {"interview_mode": "assessment"}]
)
def test_roles_creation_always_persists_and_returns_assessment(authoring, purpose):
    client, repo = authoring
    response = client.post(
        "/v2/assessment-roles", json={"jobName": "Backend Engineer", **purpose}
    )
    assert response.status_code == 201, response.text
    assert response.json()["interviewMode"] == "assessment"
    assert repo.create_profile.await_args.kwargs["interview_mode"] == "assessment"
    assert repo.create_profile.await_args.kwargs["created_by"] == 7


@pytest.mark.parametrize(
    "method,path",
    [("post", "/v2/assessment-roles"), ("put", "/v2/assessment-roles/41")],
)
@pytest.mark.parametrize("field", ["interviewMode", "interview_mode"])
def test_practice_payload_cannot_enter_assessment_authoring(
    authoring, method, path, field
):
    client, repo = authoring
    response = getattr(client, method)(
        path, json={"jobName": "Backend Engineer", field: "practice"}
    )
    assert response.status_code == 422
    repo.create_profile.assert_not_awaited()
    repo.update_profile.assert_not_awaited()


def test_assessment_edit_requests_atomic_purpose_check(authoring):
    client, repo = authoring
    response = client.put("/v2/assessment-roles/41", json={"jobName": "Updated"})
    assert response.status_code == 200, response.text
    assert repo.update_profile.await_args.kwargs == {
        "profile_id": 41,
        "update_data": {"job_name": "Updated"},
        "expected_interview_mode": "assessment",
    }


def test_assessment_can_submit_and_publish_with_confirmed_purpose(authoring):
    client, repo = authoring
    submitted = profile()
    submitted.status = "under_review"
    submitted.submitted_at = datetime.datetime.now(datetime.timezone.utc)
    repo.get_by_id = AsyncMock(return_value=profile())
    repo.get_job_profile_questions = AsyncMock(
        return_value=[SimpleNamespace(level=1) for _ in range(5)]
    )
    repo.submit_profile.return_value = submitted
    response = client.post("/v2/assessment-roles/41/submit")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "under_review"
    assert response.json()["totalQuestions"] == 5
    published = profile()
    published.status = "published"
    repo.update_review_status.return_value = published
    response = client.patch(
        "/v2/assessment-roles/41/review",
        json={"status": "published", "adminComment": "Reviewed content"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["interviewMode"] == "assessment"
    assert response.json()["status"] == "published"
    assert repo.get_assessment_for_update.await_count == 2


def test_intentional_practice_api_is_preserved(authoring):
    client, repo = authoring
    repo.create_profile.return_value = profile("practice")
    response = client.post(
        "/v2/job-profiles",
        json={"jobName": "Practice role", "interviewMode": "practice"},
    )
    assert response.status_code == 201
    assert response.json()["interviewMode"] == "practice"


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("post", "/v2/assessment-roles/41/submit", {}),
        (
            "patch",
            "/v2/assessment-roles/41/review",
            {"status": "published", "adminComment": "Reviewed"},
        ),
    ],
)
def test_legacy_practice_cannot_be_published_through_roles(
    authoring, method, path, payload
):
    client, repo = authoring
    repo.get_assessment_for_update.side_effect = fastapi.HTTPException(
        409, "Practice template requires review"
    )
    response = getattr(client, method)(path, json=payload)
    assert response.status_code == 409, response.text
    repo.submit_profile.assert_not_awaited()
    repo.update_review_status.assert_not_awaited()


@pytest.mark.anyio
async def test_locked_publication_lookup_rejects_practice():
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = profile("practice")
    session.execute = AsyncMock(return_value=result)
    with pytest.raises(fastapi.HTTPException) as error:
        await JobProfileCRUDRepository(session).get_assessment_for_update(41)
    assert error.value.status_code == 409
    assert "FOR UPDATE" in str(session.execute.await_args.args[0])


@pytest.mark.anyio
async def test_stale_practice_draft_cannot_be_repurposed_or_saved():
    session = MagicMock()
    result = MagicMock()
    old = profile("practice")
    result.scalar_one_or_none.return_value = old
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    repo = JobProfileCRUDRepository(session)
    with pytest.raises(fastapi.HTTPException) as error:
        await repo.update_profile(
            41, {"job_name": "Updated"}, expected_interview_mode="assessment"
        )
    assert error.value.status_code == 409
    assert old.interview_mode == "practice"
    assert old.job_name == "Backend Engineer"
    session.commit.assert_not_awaited()
    assert "FOR UPDATE" in str(session.execute.await_args.args[0])


@pytest.mark.anyio
async def test_student_attempt_snapshots_assessment_from_created_role():
    session = MagicMock()
    session.get = AsyncMock(return_value=profile())
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    repo = InterviewCRUDRepository(session)
    sitting = await repo.create_interview(
        user_id=8, track="Backend Engineer", job_profile_id=41
    )
    assert sitting.interview_mode == "assessment"
    assert sitting.expires_at is None
    session.add.assert_called_once_with(sitting)


def test_creator_cannot_bypass_administrator_review_on_generic_route(authoring):
    client, repo = authoring
    client.app.dependency_overrides[get_current_creator_user] = lambda: SimpleNamespace(
        id=7, is_admin=False
    )
    response = client.patch("/v2/job-profiles/41/review", json={"status": "published"})
    assert response.status_code == 403
    repo.update_review_status.assert_not_awaited()
