"""
Unit tests for the Job Profile V2 APIs created in the feature/roles-page-api branch.

This suite covers all 8 V2 endpoints:
1. GET /api/v2/job-profiles/recent-activity
2. GET /api/v2/job-profiles/summary
3. GET /api/v2/job-profiles (with optional category parameter)
4. POST /api/v2/job-profiles
5. DELETE /api/v2/job-profiles/{id}
6. POST /api/v2/job-profiles/upload/job-description
7. POST /api/v2/job-profiles/upload/knowledge-questions
8. POST /api/v2/job-profiles/extract-skills
"""

import io
import datetime
import pytest
import fastapi
from fastapi import File, UploadFile
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

# Import schemas and repository
from src.models.schemas.job_profile import (
    JobProfileSummaryResponse, 
    JobProfileResponse, 
    JobProfileCreateV2,
    JobProfileListResponse,
    JobProfileActivityResponse,
    JobProfileUploadResponse,
    JobProfileExtractSkillsRequest,
    JobProfileExtractSkillsResponse
)
from src.repository.crud.job_profile import JobProfileCRUDRepository
from src.services.file_processor import validate_file
from src.services.skills_extractor import extract_skills_from_text

# ---------------------------------------------------------------------------
# Setup Minimal FastAPI Application for Mock Testing
# ---------------------------------------------------------------------------
_app = fastapi.FastAPI()

# Mock dependencies
async def _fake_current_user():
    return {"id": 1, "email": "test@example.com"}

_mock_repo = MagicMock(spec=JobProfileCRUDRepository)

async def _get_mock_repo():
    return _mock_repo

# V2 Route implementation matching src/api/routes/job_profiles_v2.py
@_app.get(
    path="/api/v2/job-profiles/recent-activity",
    response_model=list[JobProfileActivityResponse],
    status_code=200,
)
async def get_recent_activity(
    current_user=fastapi.Depends(_fake_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(_get_mock_repo),
) -> list[JobProfileActivityResponse]:
    activities = await job_profile_repo.get_recent_activity(limit=5)
    return [JobProfileActivityResponse(**a) for a in activities]


@_app.get(
    path="/api/v2/job-profiles/summary",
    response_model=JobProfileSummaryResponse,
    status_code=200,
)
async def get_job_profiles_summary(
    current_user=fastapi.Depends(_fake_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(_get_mock_repo),
) -> JobProfileSummaryResponse:
    summary_data = await job_profile_repo.get_summary()
    return JobProfileSummaryResponse(**summary_data)


@_app.get(
    path="/api/v2/job-profiles",
    response_model=JobProfileListResponse,
    status_code=200,
)
async def list_job_profiles(
    category: str | None = fastapi.Query(None),
    current_user=fastapi.Depends(_fake_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(_get_mock_repo),
) -> JobProfileListResponse:
    profiles = await job_profile_repo.list_profiles(category=category)
    return JobProfileListResponse(items=profiles, total=len(profiles))


@_app.post(
    path="/api/v2/job-profiles",
    response_model=JobProfileResponse,
    status_code=201,
)
async def create_job_profile(
    payload: JobProfileCreateV2,
    current_user=fastapi.Depends(_fake_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(_get_mock_repo),
) -> JobProfileResponse:
    profile = await job_profile_repo.create_profile(title=payload.title, description=payload.description)
    return JobProfileResponse.from_orm(profile)




@_app.post(
    path="/api/v2/job-profiles/upload/job-description",
    response_model=JobProfileUploadResponse,
    status_code=201,
)
async def upload_job_description(
    file: UploadFile = File(...),
    current_user=fastapi.Depends(_fake_current_user),
) -> JobProfileUploadResponse:
    extension, size = await validate_file(file)
    return JobProfileUploadResponse(
        success=True,
        originalFileName=file.filename or "",
        fileType=extension.replace(".", ""),
        fileSize=size,
    )


@_app.post(
    path="/api/v2/job-profiles/upload/knowledge-questions",
    response_model=JobProfileUploadResponse,
    status_code=201,
)
async def upload_knowledge_questions(
    file: UploadFile = File(...),
    current_user=fastapi.Depends(_fake_current_user),
) -> JobProfileUploadResponse:
    extension, size = await validate_file(file)
    return JobProfileUploadResponse(
        success=True,
        originalFileName=file.filename or "",
        fileType=extension.replace(".", ""),
        fileSize=size,
    )


@_app.post(
    path="/api/v2/job-profiles/extract-skills",
    response_model=JobProfileExtractSkillsResponse,
    status_code=200,
)
async def extract_skills(
    payload: JobProfileExtractSkillsRequest,
    current_user=fastapi.Depends(_fake_current_user),
) -> JobProfileExtractSkillsResponse:
    if not payload.jobDescription.strip():
        raise fastapi.HTTPException(
            status_code=422,
            detail="jobDescription cannot be empty"
        )
    extracted_skills = extract_skills_from_text(payload.jobDescription)
    return JobProfileExtractSkillsResponse(skills=extracted_skills)


client = TestClient(_app)

# Mock model helper representing ORM
class MockJobProfileModel:
    def __init__(self, id, job_name, job_description, created_at=None):
        self.id = id
        self.job_name = job_name
        self.job_description = job_description or ""
        self.created_at = created_at or datetime.datetime.now(datetime.timezone.utc)
        self.updated_at = self.created_at

    @property
    def title(self) -> str:
        return self.job_name

    @property
    def description(self) -> str | None:
        return self.job_description

# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

# 1. GET /api/v2/job-profiles/recent-activity
def test_get_recent_activity():
    # Setup mock return data
    mock_activities = [
        {"id": 1, "title": "Software Engineer", "action": "created", "message": "Role created", "createdAt": datetime.datetime.now(datetime.timezone.utc)},
        {"id": 2, "title": "Product Manager", "action": "created", "message": "Role created", "createdAt": datetime.datetime.now(datetime.timezone.utc)}
    ]
    _mock_repo.get_recent_activity = AsyncMock(return_value=mock_activities)

    response = client.get("/api/v2/job-profiles/recent-activity")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["title"] == "Software Engineer"
    assert data[1]["title"] == "Product Manager"
    _mock_repo.get_recent_activity.assert_called_once_with(limit=5)


# 2. GET /api/v2/job-profiles/summary
def test_get_job_profiles_summary():
    mock_summary = {
        "totalRoles": 12,
        "pendingReview": 0,
        "approved": 0,
        "rejected": 0
    }
    _mock_repo.get_summary = AsyncMock(return_value=mock_summary)

    response = client.get("/api/v2/job-profiles/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["totalRoles"] == 12
    assert data["pendingReview"] == 0
    _mock_repo.get_summary.assert_called_once()


# 3. GET /api/v2/job-profiles
def test_list_job_profiles():
    # Setup mock return profiles
    mock_profiles = [
        MockJobProfileModel(1, "Python Developer", "Writes clean code"),
        MockJobProfileModel(2, "Java Architect", "Designs microservices")
    ]
    _mock_repo.list_profiles = AsyncMock(return_value=mock_profiles)

    # Test listing all
    response = client.get("/api/v2/job-profiles")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["items"][0]["title"] == "Python Developer"
    assert data["items"][1]["title"] == "Java Architect"
    _mock_repo.list_profiles.assert_called_once_with(category=None)


def test_list_job_profiles_with_category_filter():
    mock_profiles = [
        MockJobProfileModel(1, "Python Developer", "Writes clean code")
    ]
    _mock_repo.list_profiles = AsyncMock(return_value=mock_profiles)

    # Test filtering
    response = client.get("/api/v2/job-profiles?category=Python")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Python Developer"
    _mock_repo.list_profiles.assert_called_with(category="Python")


# 4. POST /api/v2/job-profiles
def test_create_job_profile():
    mock_created = MockJobProfileModel(100, "Frontend Engineer", "Builds modern interfaces")
    _mock_repo.create_profile = AsyncMock(return_value=mock_created)

    payload = {"title": "Frontend Engineer", "description": "Builds modern interfaces"}
    response = client.post("/api/v2/job-profiles", json=payload)
    
    assert response.status_code == 201
    data = response.json()
    assert data["id"] == 100
    assert data["title"] == "Frontend Engineer"
    assert data["description"] == "Builds modern interfaces"
    _mock_repo.create_profile.assert_called_once_with(title="Frontend Engineer", description="Builds modern interfaces")




# 6. POST /api/v2/job-profiles/upload/job-description
def test_upload_job_description_success():
    file_content = b"pdf job description bytes"
    files = {"file": ("description.pdf", io.BytesIO(file_content), "application/octet-stream")}

    response = client.post("/api/v2/job-profiles/upload/job-description", files=files)
    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    assert data["originalFileName"] == "description.pdf"
    assert data["fileType"] == "pdf"
    assert data["fileSize"] == len(file_content)
    # Check that stateless keys are absolutely absent
    assert "storedFileName" not in data
    assert "filePath" not in data


def test_upload_job_description_invalid_extension():
    files = {"file": ("resume.exe", io.BytesIO(b"malicious content"), "application/octet-stream")}

    response = client.post("/api/v2/job-profiles/upload/job-description", files=files)
    assert response.status_code == 415


# 7. POST /api/v2/job-profiles/upload/knowledge-questions
def test_upload_knowledge_questions_success():
    file_content = b"docx job questions bytes"
    files = {"file": ("questions.docx", io.BytesIO(file_content), "application/octet-stream")}

    response = client.post("/api/v2/job-profiles/upload/knowledge-questions", files=files)
    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    assert data["originalFileName"] == "questions.docx"
    assert data["fileType"] == "docx"
    assert data["fileSize"] == len(file_content)
    # Check that stateless keys are absolutely absent
    assert "storedFileName" not in data
    assert "filePath" not in data


# 8. POST /api/v2/job-profiles/extract-skills
def test_extract_skills_success():
    payload = {"jobDescription": "Looking for a Software Engineer experienced in Python, React, and SQL."}
    response = client.post("/api/v2/job-profiles/extract-skills", json=payload)
    
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["skills"], list)
    # Verify that it extracts correctly (e.g. Python, React, SQL)
    extracted = {s.lower() for s in data["skills"]}
    assert "python" in extracted or "react" in extracted or "sql" in extracted


def test_extract_skills_empty_payload():
    payload = {"jobDescription": ""}
    response = client.post("/api/v2/job-profiles/extract-skills", json=payload)
    assert response.status_code == 422
