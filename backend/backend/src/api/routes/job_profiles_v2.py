import fastapi
from fastapi import File, UploadFile
from typing import List, Optional
import logging
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.repository import get_repository
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
    JobProfileUpdateQuestionResponse
)
from src.services.file_processor import validate_file
from src.services.skills_extractor import extract_skills_from_text
from src.repository.crud.job_profile import JobProfileCRUDRepository
from src.services.llm import generate_interview_questions_with_llm
from src.services.syllabus_service import syllabus_service

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(prefix="/v2", tags=["job-profiles"])

@router.get(
    path="/job-profiles/recent-activity",
    name="job-profiles:recent-activity",
    response_model=List[JobProfileActivityResponse],
    status_code=fastapi.status.HTTP_200_OK,
    summary="Get recent role-related activity",
)
async def get_recent_activity(
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> List[JobProfileActivityResponse]:
    """
    Returns the latest 5 activities related to job profiles.
    Derived from the job_profile table records.
    """
    activities = await job_profile_repo.get_recent_activity(limit=5)
    return [JobProfileActivityResponse(**a) for a in activities]

@router.get(
    path="/job-profiles/summary",
    name="job-profiles:summary",
    response_model=JobProfileSummaryResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Get summary counts for Job Profiles",
)
async def get_job_profiles_summary(
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> JobProfileSummaryResponse:
    """
    Returns summary counts for the Roles page cards:
    - Total Roles (real count)
    - Pending Review (default 0)
    - Approved (default 0)
    - Rejected (default 0)
    """
    summary_data = await job_profile_repo.get_summary()
    return JobProfileSummaryResponse(**summary_data)

@router.get(
    path="/job-profiles",
    name="job-profiles:list",
    response_model=JobProfileListResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="List all Job Profiles",
)
async def list_job_profiles(
    category: Optional[str] = fastapi.Query(None, description="Filter by category (matches title temporarily)"),
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> JobProfileListResponse:
    profiles = await job_profile_repo.list_profiles(category=category)
    return JobProfileListResponse(items=profiles, total=len(profiles))

@router.post(
    path="/job-profiles",
    name="job-profiles:create",
    response_model=JobProfileResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Create a new Job Profile",
)
async def create_job_profile(
    payload: JobProfileCreateV2,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> JobProfileResponse:
    profile = await job_profile_repo.create_profile(title=payload.title, description=payload.description)
    return JobProfileResponse.from_orm(profile)



@router.post(
    path="/job-profiles/upload/job-description",
    name="job-profiles:upload-jd",
    response_model=JobProfileUploadResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Upload Job Description file",
)
async def upload_job_description(
    file: UploadFile = File(..., description="PDF or DOC/DOCX file (max 10MB)"),
    current_user=fastapi.Depends(get_current_user),
) -> JobProfileUploadResponse:
    """
    Validates and processes the uploaded Job Description file entirely in memory.
    No file is written to disk and no metadata is persisted.
    """
    extension, size = await validate_file(file)
    return JobProfileUploadResponse(
        success=True,
        originalFileName=file.filename or "",
        fileType=extension.replace(".", ""),
        fileSize=size,
    )


@router.post(
    path="/job-profiles/upload/knowledge-questions",
    name="job-profiles:upload-knowledge",
    response_model=JobProfileUploadResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Upload Knowledge Set Questions file",
)
async def upload_knowledge_questions(
    file: UploadFile = File(..., description="PDF or DOC/DOCX file (max 10MB)"),
    current_user=fastapi.Depends(get_current_user),
) -> JobProfileUploadResponse:
    """
    Validates and processes the uploaded Knowledge Questions file entirely in memory.
    No file is written to disk and no metadata is persisted.
    """
    extension, size = await validate_file(file)
    return JobProfileUploadResponse(
        success=True,
        originalFileName=file.filename or "",
        fileType=extension.replace(".", ""),
        fileSize=size,
    )

@router.post(
    path="/job-profiles/extract-skills",
    name="job-profiles:extract-skills",
    response_model=JobProfileExtractSkillsResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Extract skills from job description text",
)
async def extract_skills(
    payload: JobProfileExtractSkillsRequest,
    current_user=fastapi.Depends(get_current_user),
) -> JobProfileExtractSkillsResponse:
    """
    Extracts skills from the provided job description text.
    Currently uses keyword-based matching against a predefined skills list.
    """
    if not payload.jobDescription.strip():
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="jobDescription cannot be empty"
        )
        
    extracted_skills = extract_skills_from_text(payload.jobDescription)
    return JobProfileExtractSkillsResponse(skills=extracted_skills)


@router.post(
    path="/job-profiles/{job_profile_id}/questions/generate",
    name="job-profiles:generate-questions",
    response_model=JobProfileGenerateQuestionsResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Generate AI interview questions for a Job Profile",
)
async def generate_questions_v2(
    job_profile_id: int,
    payload: JobProfileGenerateQuestionsRequest,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
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

        logger.info(
            f"Generating {l.count} questions for Job Profile {job_profile_id} at Level {l.level} ({difficulty})"
        )

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
            logger.error(f"Failed to generate questions for Level {l.level}: {error}")
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


@router.get(
    path="/job-profiles/{job_profile_id}/questions",
    name="job-profiles:get-questions",
    response_model=JobProfileQuestionsListResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Get all generated questions for a job profile",
)
async def get_job_profile_questions_v2(
    job_profile_id: int,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
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


@router.post(
    path="/job-profiles/{job_profile_id}/questions",
    name="job-profiles:add-question",
    response_model=JobProfileAddQuestionResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Add a custom question to a job profile",
)
async def add_job_profile_question_v2(
    job_profile_id: int,
    payload: JobProfileAddQuestionRequest,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
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


@router.patch(
    path="/job-profile-questions/{question_id}",
    name="job-profiles:update-question",
    response_model=JobProfileUpdateQuestionResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Update a custom or AI question for a job profile",
)
async def update_job_profile_question_v2(
    question_id: int,
    payload: JobProfileUpdateQuestionRequest,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
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





