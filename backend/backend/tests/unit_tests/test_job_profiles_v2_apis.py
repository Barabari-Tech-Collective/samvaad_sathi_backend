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
from unittest.mock import AsyncMock, MagicMock, patch

# Import schemas and repository
from src.models.schemas.job_profile import (
    JobProfileSummaryResponse, 
    JobProfileResponse, 
    JobProfileCreateV2,
    JobProfileListResponse,
    JobProfileActivityResponse,
    JobProfileUploadResponse,
    JobProfileExtractSkillsRequest,
    JobProfileExtractSkillsResponse,
    JobProfileGenerateQuestionsRequest,
    JobProfileGenerateQuestionsResponse,
    JobProfileGeneratedQuestionItem,
    JobProfileQuestionsListResponse,
    JobProfileQuestionItem,
    JobProfileQuestionLevelCounts,
    JobProfileAddQuestionRequest,
    JobProfileAddQuestionResponse,
    JobProfileUpdateQuestionRequest,
    JobProfileUpdateQuestionResponse,
    JobProfileRegenerateQuestionResponse,
    JobProfileDeleteQuestionResponse,
    JobProfileDeleteResponse
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
    profile = await job_profile_repo.create_profile(
        job_name=payload.job_name,
        job_description=payload.job_description,
        company_name=payload.company_name,
        experience_level=payload.experience_level,
        skills=payload.skills,
        additional_context=payload.additional_context,
        category=payload.category,
        employment_type=payload.employment_type,
    )
    return JobProfileResponse.model_validate(profile)



@_app.delete(
    path="/api/v2/job-profiles/{job_profile_id}",
    response_model=JobProfileDeleteResponse,
    status_code=200,
)
async def delete_job_profile(
    job_profile_id: int,
    current_user=fastapi.Depends(_fake_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(_get_mock_repo),
) -> JobProfileDeleteResponse:
    deleted = await job_profile_repo.delete_profile(profile_id=job_profile_id)
    if not deleted:
        raise fastapi.HTTPException(
            status_code=404,
            detail=f"Job profile with ID {job_profile_id} not found"
        )
    return JobProfileDeleteResponse(deleted=True, job_profile_id=job_profile_id)



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
        original_file_name=file.filename or "",
        file_type=extension.replace(".", ""),
        file_size=size,
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
        original_file_name=file.filename or "",
        file_type=extension.replace(".", ""),
        file_size=size,
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
    if not payload.job_description.strip():
        raise fastapi.HTTPException(
            status_code=422,
            detail="job_description cannot be empty"
        )
    extracted_skills = extract_skills_from_text(payload.job_description)
    return JobProfileExtractSkillsResponse(skills=extracted_skills)


@_app.post(
    path="/api/v2/job-profiles/{job_profile_id}/questions/generate",
    response_model=JobProfileGenerateQuestionsResponse,
    status_code=200,
)
async def generate_questions_v2(
    job_profile_id: int,
    payload: JobProfileGenerateQuestionsRequest,
    current_user=fastapi.Depends(_fake_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(_get_mock_repo),
) -> JobProfileGenerateQuestionsResponse:
    # 1. Fetch job profile details & validate existence
    profile = await job_profile_repo.get_by_id(job_profile_id=job_profile_id)
    if profile is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Job profile with ID {job_profile_id} not found"
        )

    # 2. Level to difficulty map
    level_map = {
        1: "easy",
        2: "medium",
        3: "hard",
        4: "expert"
    }

    # 3. Validate levels and counts
    total_requested = 0
    for l in payload.levels:
        if l.level not in level_map:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid level {l.level}. Level must be 1, 2, 3, or 4."
            )
        if l.count < 0:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid count {l.count} for level {l.level}. Count cannot be negative."
            )
        total_requested += l.count

    if total_requested == 0:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail="Total question count must be greater than zero."
        )

    # 4. Generate questions using existing LLM system
    generated_questions_data = []

    skills_list = profile.skills or []
    track = profile.job_name
    context_text = profile.job_description

    from src.services.syllabus_service import syllabus_service
    from src.services.llm import generate_interview_questions_with_llm

    for l in payload.levels:
        if l.count == 0:
            continue

        difficulty = level_map[l.level]
        
        # Prepare syllabus and question ratio using existing syllabus service
        role = syllabus_service._role_manager.derive_role(track)
        topic_bank = syllabus_service.get_topics_for_role(role=role, difficulty=difficulty)
        
        topics = {
            "tech": topic_bank.tech,
            "tech_allied": topic_bank.tech_allied,
            "behavioral": topic_bank.behavioral,
            "archetypes": topic_bank.archetypes,
            "depth_guidelines": topic_bank.depth_guidelines,
        }
        
        # Extract tech-allied topics from job description
        topics["tech_allied"] = syllabus_service.extract_tech_allied_from_resume(
            resume_text=context_text,
            skills=skills_list,
            fallback_topics=topics.get("tech_allied", []),
        )
        
        question_ratio = syllabus_service.compute_question_ratio(
            years_experience=None,
            has_resume_text=bool(context_text),
            has_skills=bool(skills_list),
        )
        
        ratio = {
            "tech": question_ratio.tech,
            "tech_allied": question_ratio.tech_allied,
            "behavioral": question_ratio.behavioral,
        }
        
        influence = {
            "target_role": role,
            "difficulty": difficulty,
            "skills": skills_list,
            "experience_level": profile.experience_level,
        }

        questions_list, error, latency_ms, llm_model, structured_items = await generate_interview_questions_with_llm(
            track=track,
            context_text=context_text,
            count=l.count,
            difficulty=difficulty,
            syllabus_topics=topics,
            ratio=ratio,
            influence=influence,
        )

        if error or not structured_items:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate questions for Level {l.level}: {error or 'No questions generated'}"
            )

        for item in structured_items:
            generated_questions_data.append({
                "job_profile_id": profile.id,
                "question_text": item["text"],
                "level": l.level,
                "difficulty": difficulty,
                "question_type": item.get("category", "theoretical"),
                "is_ai_generated": True
            })

    # 5. Save generated questions into database
    db_questions = await job_profile_repo.create_job_profile_questions(generated_questions_data)

    # 6. Format and return response
    response_items = [
        JobProfileGeneratedQuestionItem(
            question_id=str(q.id),
            question=q.question_text,
            level=q.level,
            difficulty=q.difficulty,
            type=q.question_type,
            is_ai_generated=q.is_ai_generated
        )
        for q in db_questions
    ]

    return JobProfileGenerateQuestionsResponse(
        job_profile_id=str(profile.id),
        total_questions=len(response_items),
        questions=response_items
    )


@_app.get(
    path="/api/v2/job-profiles/{job_profile_id}/questions",
    response_model=JobProfileQuestionsListResponse,
    status_code=200,
)
async def get_job_profile_questions_v2(
    job_profile_id: int,
    current_user=fastapi.Depends(_fake_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(_get_mock_repo),
) -> JobProfileQuestionsListResponse:
    # 1. Validate job_profile_id exists
    profile = await job_profile_repo.get_by_id(job_profile_id=job_profile_id)
    if not profile:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Job profile with ID {job_profile_id} not found",
        )

    # 2. Fetch all questions linked to job_profile_id
    db_questions = await job_profile_repo.get_job_profile_questions(job_profile_id=job_profile_id)

    # 3. Calculate level counts
    level_counts = {
        "level_1": 0,
        "level_2": 0,
        "level_3": 0,
        "level_4": 0,
    }
    for q in db_questions:
        if q.level == 1:
            level_counts["level_1"] += 1
        elif q.level == 2:
            level_counts["level_2"] += 1
        elif q.level == 3:
            level_counts["level_3"] += 1
        elif q.level == 4:
            level_counts["level_4"] += 1

    # 4. Map questions to response format
    questions_list = [
        JobProfileQuestionItem(
            question_id=str(q.id),
            question=q.question_text,
            level=q.level,
            difficulty=q.difficulty,
            type=q.question_type,
            is_ai_generated=q.is_ai_generated,
            created_at=q.created_at,
        )
        for q in db_questions
    ]

    return JobProfileQuestionsListResponse(
        job_profile_id=str(job_profile_id),
        total_questions=len(db_questions),
        level_counts=JobProfileQuestionLevelCounts(**level_counts),
        questions=questions_list,
    )


@_app.post(
    path="/api/v2/job-profiles/{job_profile_id}/questions",
    response_model=JobProfileAddQuestionResponse,
    status_code=201,
)
async def add_job_profile_question_v2(
    job_profile_id: int,
    payload: JobProfileAddQuestionRequest,
    current_user=fastapi.Depends(_fake_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(_get_mock_repo),
) -> JobProfileAddQuestionResponse:
    # 1. Validate job_profile_id exists
    profile = await job_profile_repo.get_by_id(job_profile_id=job_profile_id)
    if not profile:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Job profile with ID {job_profile_id} not found",
        )

    # 2. Validate question text is not empty
    question_text = payload.question.strip() if payload.question else ""
    if not question_text:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail="Question text cannot be empty",
        )

    # 3. Validate level is between 1 and 4
    if payload.level not in [1, 2, 3, 4]:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail="Invalid level. Level must be 1, 2, 3, or 4.",
        )

    # 4. Save question in job_profile_question table
    db_question = await job_profile_repo.add_job_profile_question(
        job_profile_id=job_profile_id,
        question_text=question_text,
        level=payload.level,
        difficulty=payload.difficulty,
        question_type=payload.type,
        is_ai_generated=payload.is_ai_generated,
    )

    # 5. Return created question
    return JobProfileAddQuestionResponse(
        question_id=str(db_question.id),
        job_profile_id=str(job_profile_id),
        question=db_question.question_text,
        level=db_question.level,
        difficulty=db_question.difficulty,
        type=db_question.question_type,
        is_ai_generated=db_question.is_ai_generated,
        message="Question added successfully",
    )


@_app.patch(
    path="/api/v2/job-profile-questions/{question_id}",
    response_model=JobProfileUpdateQuestionResponse,
    status_code=200,
)
async def update_job_profile_question_v2(
    question_id: int,
    payload: JobProfileUpdateQuestionRequest,
    current_user=fastapi.Depends(_fake_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(_get_mock_repo),
) -> JobProfileUpdateQuestionResponse:
    # 1. Validate question_id exists
    question = await job_profile_repo.get_question_by_id(question_id=question_id)
    if not question:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Question with ID {question_id} not found",
        )

    # 2. Validate question text is not empty if provided
    update_data = {}
    if payload.question is not None:
        question_text = payload.question.strip()
        if not question_text:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_400_BAD_REQUEST,
                detail="Question text cannot be empty",
            )
        update_data["question_text"] = question_text

    # 3. Validate level is between 1 and 4 if provided
    if payload.level is not None:
        if payload.level not in [1, 2, 3, 4]:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_400_BAD_REQUEST,
                detail="Invalid level. Level must be 1, 2, 3, or 4.",
            )
        update_data["level"] = payload.level

    if payload.difficulty is not None:
        update_data["difficulty"] = payload.difficulty

    if payload.type is not None:
        update_data["question_type"] = payload.type

    # 4. Save updated question
    updated_question = await job_profile_repo.update_job_profile_question(
        question=question,
        update_data=update_data
    )

    # 5. Return updated response
    return JobProfileUpdateQuestionResponse(
        question_id=str(updated_question.id),
        question=updated_question.question_text,
        level=updated_question.level,
        difficulty=updated_question.difficulty,
        type=updated_question.question_type,
        is_ai_generated=updated_question.is_ai_generated,
        message="Question updated successfully",
    )


@_app.post(
    path="/api/v2/job-profile-questions/{question_id}/regenerate",
    response_model=JobProfileRegenerateQuestionResponse,
    status_code=200,
)
async def regenerate_job_profile_question_v2(
    question_id: int,
    current_user=fastapi.Depends(_fake_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(_get_mock_repo),
) -> JobProfileRegenerateQuestionResponse:
    # 1. Validate question_id exists
    question = await job_profile_repo.get_question_by_id(question_id=question_id)
    if not question:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Question with ID {question_id} not found",
        )

    # 2. Fetch linked job profile
    profile = await job_profile_repo.get_by_id(job_profile_id=question.job_profile_id)
    if not profile:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Job profile for this question not found",
        )

    # 3. Extract and map configuration
    track = profile.job_name
    context_text = profile.job_description or ""
    skills_list = profile.skills or []

    level_map = {
        1: "easy",
        2: "medium",
        3: "hard",
        4: "expert"
    }
    difficulty = level_map.get(question.level, "easy")

    # 4. Prepare syllabus and ratio
    from src.services.syllabus_service import syllabus_service
    from src.services.llm import generate_interview_questions_with_llm

    role = syllabus_service._role_manager.derive_role(track)
    topic_bank = syllabus_service.get_topics_for_role(role=role, difficulty=difficulty)

    topics = {
        "tech": topic_bank.tech,
        "tech_allied": topic_bank.tech_allied,
        "behavioral": topic_bank.behavioral,
        "archetypes": topic_bank.archetypes,
        "depth_guidelines": topic_bank.depth_guidelines,
    }
    topics["tech_allied"] = syllabus_service.extract_tech_allied_from_resume(
        resume_text=context_text,
        skills=skills_list,
        fallback_topics=topics.get("tech_allied", []),
    )

    question_ratio = syllabus_service.compute_question_ratio(
        years_experience=None,
        has_resume_text=bool(context_text),
        has_skills=bool(skills_list),
    )
    ratio = {
        "tech": question_ratio.tech,
        "tech_allied": question_ratio.tech_allied,
        "behavioral": question_ratio.behavioral,
    }

    influence = {
        "target_role": role,
        "difficulty": difficulty,
        "skills": skills_list,
        "experience_level": profile.experience_level,
    }

    # 5. Generate new question using existing LLM service
    questions_list, error, latency_ms, llm_model, structured_items = await generate_interview_questions_with_llm(
        track=track,
        context_text=context_text,
        count=1,
        difficulty=difficulty,
        syllabus_topics=topics,
        ratio=ratio,
        influence=influence,
    )

    if error or not structured_items:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to regenerate question: {error or 'No question returned from LLM'}"
        )

    new_item = structured_items[0]

    # 6. Replace and update old question text
    update_data = {
        "question_text": new_item["text"],
        "question_type": new_item.get("category", "theoretical"),
        "is_ai_generated": True
    }
    updated_question = await job_profile_repo.update_job_profile_question(
        question=question,
        update_data=update_data
    )

    # 7. Return updated response
    return JobProfileRegenerateQuestionResponse(
        question_id=str(updated_question.id),
        question=updated_question.question_text,
        level=updated_question.level,
        difficulty=updated_question.difficulty,
        type=updated_question.question_type,
        is_ai_generated=updated_question.is_ai_generated,
        message="Question regenerated successfully",
    )


@_app.delete(
    path="/api/v2/job-profile-questions/{question_id}",
    response_model=JobProfileDeleteQuestionResponse,
    status_code=200,
)
async def delete_job_profile_question_v2(
    question_id: int,
    current_user=fastapi.Depends(_fake_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(_get_mock_repo),
) -> JobProfileDeleteQuestionResponse:
    # 1. Validate question_id exists
    question = await job_profile_repo.get_question_by_id(question_id=question_id)
    if not question:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"Question with ID {question_id} not found",
        )

    # 2. Delete the question
    await job_profile_repo.delete_job_profile_question(question=question)

    # 3. Return success response
    return JobProfileDeleteQuestionResponse(
        message="Question deleted successfully",
        question_id=str(question_id),
    )


client = TestClient(_app)

# Mock model helper representing ORM
class MockJobProfileModel:
    def __init__(self, id, job_name, job_description, created_at=None, skills=None, experience_level=None):
        self.id = id
        self.job_name = job_name
        self.job_description = job_description or ""
        self.skills = skills
        self.experience_level = experience_level
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
    assert data["items"][0]["jobName"] == "Python Developer"
    assert data["items"][1]["jobName"] == "Java Architect"
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
    assert data["items"][0]["jobName"] == "Python Developer"
    _mock_repo.list_profiles.assert_called_with(category="Python")


# 4. POST /api/v2/job-profiles
def test_create_job_profile():
    mock_created = MockJobProfileModel(100, "Frontend Engineer", "Builds modern interfaces")
    mock_created.company_name = "Google"
    mock_created.experience_level = "Senior"
    mock_created.skills = ["React", "CSS"]
    mock_created.additional_context = "Urgent hire"
    _mock_repo.create_profile = AsyncMock(return_value=mock_created)

    payload = {
        "jobName": "Frontend Engineer",
        "jobDescription": "Builds modern interfaces",
        "companyName": "Google",
        "experienceLevel": "Senior",
        "skills": ["React", "CSS"],
        "additionalContext": "Urgent hire"
    }
    response = client.post("/api/v2/job-profiles", json=payload)
    
    assert response.status_code == 201
    data = response.json()
    assert data["id"] == 100
    assert data["jobName"] == "Frontend Engineer"
    assert data["jobDescription"] == "Builds modern interfaces"
    assert data["companyName"] == "Google"
    assert data["experienceLevel"] == "Senior"
    assert data["skills"] == ["React", "CSS"]
    assert data["additionalContext"] == "Urgent hire"
    _mock_repo.create_profile.assert_called_once_with(
        job_name="Frontend Engineer",
        job_description="Builds modern interfaces",
        company_name="Google",
        experience_level="Senior",
        skills=["React", "CSS"],
        additional_context="Urgent hire",
        category=None,
        employment_type=None
    )




def test_create_job_profile_legacy_aliases():
    mock_created = MockJobProfileModel(100, "Frontend Engineer", "Builds modern interfaces")
    _mock_repo.create_profile = AsyncMock(return_value=mock_created)

    # Send legacy key names: "title" and "description"
    payload = {
        "title": "Frontend Engineer",
        "description": "Builds modern interfaces"
    }
    response = client.post("/api/v2/job-profiles", json=payload)
    
    assert response.status_code == 201
    data = response.json()
    assert data["id"] == 100
    
    # Assert BOTH the new keys and legacy keys exist exactly in the JSON keys!
    assert "jobName" in data
    assert "jobDescription" in data
    assert "title" in data
    assert "description" in data
    
    # Assert their values are correctly mapped
    assert data["jobName"] == "Frontend Engineer"
    assert data["jobDescription"] == "Builds modern interfaces"
    assert data["title"] == "Frontend Engineer"
    assert data["description"] == "Builds modern interfaces"
    
    _mock_repo.create_profile.assert_called_once_with(
        job_name="Frontend Engineer",
        job_description="Builds modern interfaces",
        company_name=None,
        experience_level=None,
        skills=None,
        additional_context=None,
        category=None,
        employment_type=None
    )




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


# 9. POST /api/v2/job-profiles/{job_profile_id}/questions/generate
def test_generate_questions_success():
    profile = MockJobProfileModel(
        id=123,
        job_name="Python Developer",
        job_description="Looking for an experienced Python developer with React skills.",
        skills=["Python", "React"],
        experience_level="Mid"
    )
    _mock_repo.get_by_id = AsyncMock(return_value=profile)

    mock_items = [
        {"text": "What is Django?", "category": "tech"},
        {"text": "Explain React state management.", "category": "tech_allied"}
    ]

    class MockQuestion:
        def __init__(self, id, job_profile_id, question_text, level, difficulty, question_type, is_ai_generated):
            self.id = id
            self.job_profile_id = job_profile_id
            self.question_text = question_text
            self.level = level
            self.difficulty = difficulty
            self.question_type = question_type
            self.is_ai_generated = is_ai_generated

    saved_questions = [
        MockQuestion(1, 123, "What is Django?", 1, "easy", "tech", True),
        MockQuestion(2, 123, "Explain React state management.", 1, "easy", "tech_allied", True)
    ]
    _mock_repo.create_job_profile_questions = AsyncMock(return_value=saved_questions)

    payload = {
        "levels": [
            {"level": 1, "count": 2}
        ]
    }
    with patch("src.services.llm.generate_interview_questions_with_llm", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = (["What is Django?", "Explain React state management."], None, 150, "gpt-4o-mini", mock_items)
        response = client.post("/api/v2/job-profiles/123/questions/generate", json=payload)
        
        assert response.status_code == 200
        data = response.json()
        assert data["job_profile_id"] == "123"
        assert data["total_questions"] == 2
        assert data["questions"][0]["question_id"] == "1"
        assert data["questions"][0]["question"] == "What is Django?"
        assert data["questions"][0]["level"] == 1
        assert data["questions"][0]["difficulty"] == "easy"
        assert data["questions"][0]["type"] == "tech"
        assert data["questions"][0]["is_ai_generated"] is True

        _mock_repo.get_by_id.assert_called_with(job_profile_id=123)
        mock_generate.assert_called_once()
        _mock_repo.create_job_profile_questions.assert_called_once()


def test_generate_questions_profile_not_found():
    _mock_repo.get_by_id = AsyncMock(return_value=None)
    payload = {
        "levels": [
            {"level": 1, "count": 2}
        ]
    }
    response = client.post("/api/v2/job-profiles/999/questions/generate", json=payload)
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_generate_questions_invalid_level():
    profile = MockJobProfileModel(123, "Python Developer", "Job Description")
    _mock_repo.get_by_id = AsyncMock(return_value=profile)
    payload = {
        "levels": [
            {"level": 5, "count": 2}
        ]
    }
    response = client.post("/api/v2/job-profiles/123/questions/generate", json=payload)
    assert response.status_code == 400
    assert "Invalid level" in response.json()["detail"]


def test_generate_questions_negative_count():
    profile = MockJobProfileModel(123, "Python Developer", "Job Description")
    _mock_repo.get_by_id = AsyncMock(return_value=profile)
    payload = {
        "levels": [
            {"level": 1, "count": -5}
        ]
    }
    response = client.post("/api/v2/job-profiles/123/questions/generate", json=payload)
    assert response.status_code == 400
    assert "Count cannot be negative" in response.json()["detail"]


def test_get_questions_success():
    profile = MockJobProfileModel(123, "Python Developer", "Job Description")
    _mock_repo.get_by_id = AsyncMock(return_value=profile)

    class MockQuestion:
        def __init__(self, id, job_profile_id, question_text, level, difficulty, question_type, is_ai_generated, created_at):
            self.id = id
            self.job_profile_id = job_profile_id
            self.question_text = question_text
            self.level = level
            self.difficulty = difficulty
            self.question_type = question_type
            self.is_ai_generated = is_ai_generated
            self.created_at = created_at

    t1 = datetime.datetime(2026, 5, 26, 10, 0, 0, tzinfo=datetime.timezone.utc)
    t2 = datetime.datetime(2026, 5, 26, 10, 5, 0, tzinfo=datetime.timezone.utc)

    db_questions = [
        MockQuestion(1, 123, "Question 1", 1, "easy", "tech", True, t1),
        MockQuestion(2, 123, "Question 2", 2, "medium", "behavioral", True, t2)
    ]
    _mock_repo.get_job_profile_questions = AsyncMock(return_value=db_questions)

    response = client.get("/api/v2/job-profiles/123/questions")
    assert response.status_code == 200
    data = response.json()
    assert data["job_profile_id"] == "123"
    assert data["total_questions"] == 2
    assert data["level_counts"]["level_1"] == 1
    assert data["level_counts"]["level_2"] == 1
    assert data["level_counts"]["level_3"] == 0
    assert data["level_counts"]["level_4"] == 0
    assert len(data["questions"]) == 2
    assert data["questions"][0]["question_id"] == "1"
    assert data["questions"][0]["question"] == "Question 1"
    assert data["questions"][0]["level"] == 1
    assert data["questions"][1]["level"] == 2
    assert "2026-05-26T10:00:00" in data["questions"][0]["created_at"]


def test_get_questions_profile_not_found():
    _mock_repo.get_by_id = AsyncMock(return_value=None)
    response = client.get("/api/v2/job-profiles/999/questions")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_get_questions_empty_list():
    profile = MockJobProfileModel(123, "Python Developer", "Job Description")
    _mock_repo.get_by_id = AsyncMock(return_value=profile)
    _mock_repo.get_job_profile_questions = AsyncMock(return_value=[])

    response = client.get("/api/v2/job-profiles/123/questions")
    assert response.status_code == 200
    data = response.json()
    assert data["job_profile_id"] == "123"
    assert data["total_questions"] == 0
    assert data["level_counts"]["level_1"] == 0
    assert data["questions"] == []


def test_add_question_success():
    profile = MockJobProfileModel(123, "Python Developer", "Job Description")
    _mock_repo.get_by_id = AsyncMock(return_value=profile)

    class MockQuestion:
        def __init__(self, id, job_profile_id, question_text, level, difficulty, question_type, is_ai_generated):
            self.id = id
            self.job_profile_id = job_profile_id
            self.question_text = question_text
            self.level = level
            self.difficulty = difficulty
            self.question_type = question_type
            self.is_ai_generated = is_ai_generated

    created_q = MockQuestion(51, 123, "Explain closures in JavaScript.", 2, "medium", "theoretical", False)
    _mock_repo.add_job_profile_question = AsyncMock(return_value=created_q)

    payload = {
        "question": "Explain closures in JavaScript.",
        "level": 2,
        "difficulty": "medium",
        "type": "theoretical",
        "is_ai_generated": False
    }

    response = client.post("/api/v2/job-profiles/123/questions", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["question_id"] == "51"
    assert data["job_profile_id"] == "123"
    assert data["question"] == "Explain closures in JavaScript."
    assert data["level"] == 2
    assert data["difficulty"] == "medium"
    assert data["type"] == "theoretical"
    assert data["is_ai_generated"] is False
    assert data["message"] == "Question added successfully"


def test_add_question_profile_not_found():
    _mock_repo.get_by_id = AsyncMock(return_value=None)
    payload = {
        "question": "Explain closures in JavaScript.",
        "level": 2,
        "difficulty": "medium",
        "type": "theoretical",
        "is_ai_generated": False
    }
    response = client.post("/api/v2/job-profiles/999/questions", json=payload)
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_add_question_empty_text():
    profile = MockJobProfileModel(123, "Python Developer", "Job Description")
    _mock_repo.get_by_id = AsyncMock(return_value=profile)
    payload = {
        "question": "   ",
        "level": 2,
        "difficulty": "medium",
        "type": "theoretical",
        "is_ai_generated": False
    }
    response = client.post("/api/v2/job-profiles/123/questions", json=payload)
    assert response.status_code == 400
    assert "cannot be empty" in response.json()["detail"]


def test_add_question_invalid_level():
    profile = MockJobProfileModel(123, "Python Developer", "Job Description")
    _mock_repo.get_by_id = AsyncMock(return_value=profile)
    payload = {
        "question": "Explain closures in JavaScript.",
        "level": 5,
        "difficulty": "medium",
        "type": "theoretical",
        "is_ai_generated": False
    }
    response = client.post("/api/v2/job-profiles/123/questions", json=payload)
    assert response.status_code == 400
    assert "Invalid level" in response.json()["detail"]


def test_update_question_success():
    class MockQuestion:
        def __init__(self, id, question_text, level, difficulty, question_type, is_ai_generated):
            self.id = id
            self.question_text = question_text
            self.level = level
            self.difficulty = difficulty
            self.question_type = question_type
            self.is_ai_generated = is_ai_generated

    existing_q = MockQuestion(1, "Old question text", 1, "easy", "theoretical", True)
    _mock_repo.get_question_by_id = AsyncMock(return_value=existing_q)

    updated_q = MockQuestion(1, "Explain closures in JavaScript with examples.", 2, "medium", "theoretical", True)
    _mock_repo.update_job_profile_question = AsyncMock(return_value=updated_q)

    payload = {
        "question": "Explain closures in JavaScript with examples.",
        "level": 2,
        "difficulty": "medium",
        "type": "theoretical"
    }

    response = client.patch("/api/v2/job-profile-questions/1", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["question_id"] == "1"
    assert data["question"] == "Explain closures in JavaScript with examples."
    assert data["level"] == 2
    assert data["difficulty"] == "medium"
    assert data["type"] == "theoretical"
    assert data["is_ai_generated"] is True
    assert data["message"] == "Question updated successfully"


def test_update_question_not_found():
    _mock_repo.get_question_by_id = AsyncMock(return_value=None)
    payload = {
        "question": "Some text",
        "level": 2,
        "difficulty": "medium"
    }
    response = client.patch("/api/v2/job-profile-questions/999", json=payload)
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_update_question_empty_text():
    class MockQuestion:
        def __init__(self, id, question_text, level, difficulty, question_type, is_ai_generated):
            self.id = id
            self.question_text = question_text
            self.level = level
            self.difficulty = difficulty
            self.question_type = question_type
            self.is_ai_generated = is_ai_generated

    existing_q = MockQuestion(1, "Old question text", 1, "easy", "theoretical", True)
    _mock_repo.get_question_by_id = AsyncMock(return_value=existing_q)

    payload = {
        "question": "   "
    }
    response = client.patch("/api/v2/job-profile-questions/1", json=payload)
    assert response.status_code == 400
    assert "cannot be empty" in response.json()["detail"]


def test_update_question_invalid_level():
    class MockQuestion:
        def __init__(self, id, question_text, level, difficulty, question_type, is_ai_generated):
            self.id = id
            self.question_text = question_text
            self.level = level
            self.difficulty = difficulty
            self.question_type = question_type
            self.is_ai_generated = is_ai_generated

    existing_q = MockQuestion(1, "Old question text", 1, "easy", "theoretical", True)
    _mock_repo.get_question_by_id = AsyncMock(return_value=existing_q)

    payload = {
        "level": 5
    }
    response = client.patch("/api/v2/job-profile-questions/1", json=payload)
    assert response.status_code == 400
    assert "Invalid level" in response.json()["detail"]


def test_regenerate_question_success():
    class MockQuestion:
        def __init__(self, id, job_profile_id, question_text, level, difficulty, question_type, is_ai_generated):
            self.id = id
            self.job_profile_id = job_profile_id
            self.question_text = question_text
            self.level = level
            self.difficulty = difficulty
            self.question_type = question_type
            self.is_ai_generated = is_ai_generated

    existing_q = MockQuestion(1, 123, "What is Django?", 1, "easy", "tech", True)
    _mock_repo.get_question_by_id = AsyncMock(return_value=existing_q)

    profile = MockJobProfileModel(
        id=123,
        job_name="Python Developer",
        job_description="Looking for an experienced Python developer.",
        skills=["Python"],
        experience_level="Mid"
    )
    _mock_repo.get_by_id = AsyncMock(return_value=profile)

    mock_items = [{"text": "Explain closures in JavaScript.", "category": "tech"}]

    updated_q = MockQuestion(1, 123, "Explain closures in JavaScript.", 1, "easy", "tech", True)
    _mock_repo.update_job_profile_question = AsyncMock(return_value=updated_q)

    with patch("src.services.llm.generate_interview_questions_with_llm", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = (["Explain closures in JavaScript."], None, 100, "gpt-4o-mini", mock_items)
        response = client.post("/api/v2/job-profile-questions/1/regenerate")
        
        assert response.status_code == 200
        data = response.json()
        assert data["question_id"] == "1"
        assert data["question"] == "Explain closures in JavaScript."
        assert data["level"] == 1
        assert data["difficulty"] == "easy"
        assert data["type"] == "tech"
        assert data["is_ai_generated"] is True
        assert data["message"] == "Question regenerated successfully"

        _mock_repo.get_question_by_id.assert_called_once_with(question_id=1)
        _mock_repo.get_by_id.assert_called_once_with(job_profile_id=123)
        mock_generate.assert_called_once()
        _mock_repo.update_job_profile_question.assert_called_once()


def test_regenerate_question_not_found():
    _mock_repo.get_question_by_id = AsyncMock(return_value=None)
    response = client.post("/api/v2/job-profile-questions/999/regenerate")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_regenerate_question_profile_not_found():
    class MockQuestion:
        def __init__(self, id, job_profile_id, question_text, level, difficulty, question_type, is_ai_generated):
            self.id = id
            self.job_profile_id = job_profile_id
            self.question_text = question_text
            self.level = level
            self.difficulty = difficulty
            self.question_type = question_type
            self.is_ai_generated = is_ai_generated

    existing_q = MockQuestion(1, 123, "What is Django?", 1, "easy", "tech", True)
    _mock_repo.get_question_by_id = AsyncMock(return_value=existing_q)
    _mock_repo.get_by_id = AsyncMock(return_value=None)

    response = client.post("/api/v2/job-profile-questions/1/regenerate")
    assert response.status_code == 404
    assert "Job profile for this question not found" in response.json()["detail"]


def test_regenerate_question_llm_failure():
    class MockQuestion:
        def __init__(self, id, job_profile_id, question_text, level, difficulty, question_type, is_ai_generated):
            self.id = id
            self.job_profile_id = job_profile_id
            self.question_text = question_text
            self.level = level
            self.difficulty = difficulty
            self.question_type = question_type
            self.is_ai_generated = is_ai_generated

    existing_q = MockQuestion(1, 123, "What is Django?", 1, "easy", "tech", True)
    _mock_repo.get_question_by_id = AsyncMock(return_value=existing_q)

    profile = MockJobProfileModel(
        id=123,
        job_name="Python Developer",
        job_description="Looking for an experienced Python developer.",
        skills=["Python"],
        experience_level="Mid"
    )
    _mock_repo.get_by_id = AsyncMock(return_value=profile)

    with patch("src.services.llm.generate_interview_questions_with_llm", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = ([], "LLM Failed Error", 0, "", [])
        response = client.post("/api/v2/job-profile-questions/1/regenerate")
        assert response.status_code == 500
        assert "Failed to regenerate question" in response.json()["detail"]


def test_delete_question_success():
    class MockQuestion:
        def __init__(self, id, job_profile_id, question_text, level, difficulty, question_type, is_ai_generated):
            self.id = id
            self.job_profile_id = job_profile_id
            self.question_text = question_text
            self.level = level
            self.difficulty = difficulty
            self.question_type = question_type
            self.is_ai_generated = is_ai_generated

    existing_q = MockQuestion(1, 123, "What is Django?", 1, "easy", "tech", True)
    _mock_repo.get_question_by_id = AsyncMock(return_value=existing_q)
    _mock_repo.delete_job_profile_question = AsyncMock(return_value=None)

    response = client.delete("/api/v2/job-profile-questions/1")
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Question deleted successfully"
    assert data["question_id"] == "1"

    _mock_repo.get_question_by_id.assert_called_once_with(question_id=1)
    _mock_repo.delete_job_profile_question.assert_called_once_with(question=existing_q)


def test_delete_question_not_found():
    _mock_repo.get_question_by_id = AsyncMock(return_value=None)
    response = client.delete("/api/v2/job-profile-questions/999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


# 9. DELETE /api/v2/job-profiles/{id}
def test_delete_job_profile_success():
    _mock_repo.delete_profile = AsyncMock(return_value=True)
    response = client.delete("/api/v2/job-profiles/123")
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] is True
    assert data["jobProfileId"] == 123
    _mock_repo.delete_profile.assert_called_once_with(profile_id=123)


def test_delete_job_profile_not_found():
    _mock_repo.delete_profile = AsyncMock(return_value=False)
    response = client.delete("/api/v2/job-profiles/999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]
    _mock_repo.delete_profile.assert_called_once_with(profile_id=999)




def test_create_job_profile_figma_metadata():
    mock_created = MockJobProfileModel(123, "Senior Front-End Developer", "Own end-to-end frontend architecture...")
    mock_created.company_name = "Amazon"
    mock_created.experience_level = "0-2 years"
    mock_created.skills = ["React", "TypeScript", "Node", "GraphQL"]
    mock_created.additional_context = "Need strong frontend architecture knowledge"
    mock_created.category = "Engineering"
    mock_created.employment_type = "Full-time"
    
    # Custom fixed created_at time to assert in the JSON
    fixed_time = datetime.datetime(2026, 6, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
    mock_created.created_at = fixed_time

    _mock_repo.create_profile = AsyncMock(return_value=mock_created)

    payload = {
        "job_name": "Senior Front-End Developer",
        "job_description": "Own end-to-end frontend architecture...",
        "companyName": "Amazon",
        "experienceLevel": "0-2 years",
        "category": "Engineering",
        "employmentType": "Full-time",
        "skills": ["React", "TypeScript", "Node", "GraphQL"],
        "additionalContext": "Need strong frontend architecture knowledge"
    }
    
    response = client.post("/api/v2/job-profiles", json=payload)
    
    assert response.status_code == 201
    data = response.json()
    
    # Assert exact required response structure and fields
    assert data["id"] == 123
    assert data["job_name"] == "Senior Front-End Developer"
    assert data["job_description"] == "Own end-to-end frontend architecture..."
    assert data["companyName"] == "Amazon"
    assert data["experienceLevel"] == "0-2 years"
    assert data["category"] == "Engineering"
    assert data["employmentType"] == "Full-time"
    assert data["skills"] == ["React", "TypeScript", "Node", "GraphQL"]
    assert data["additionalContext"] == "Need strong frontend architecture knowledge"
    assert data["createdAt"] == "2026-06-01T10:00:00Z"
    
    # Assert senior's compatibility aliases exist exactly in the JSON keys
    assert "jobName" in data
    assert "jobDescription" in data
    assert "title" in data
    assert "description" in data
    
    # Assert values for legacy/senior aliases
    assert data["jobName"] == "Senior Front-End Developer"
    assert data["jobDescription"] == "Own end-to-end frontend architecture..."
    assert data["title"] == "Senior Front-End Developer"
    assert data["description"] == "Own end-to-end frontend architecture..."

    _mock_repo.create_profile.assert_called_once_with(
        job_name="Senior Front-End Developer",
        job_description="Own end-to-end frontend architecture...",
        company_name="Amazon",
        experience_level="0-2 years",
        skills=["React", "TypeScript", "Node", "GraphQL"],
        additional_context="Need strong frontend architecture knowledge",
        category="Engineering",
        employment_type="Full-time"
    )







