import fastapi
from fastapi import File, UploadFile
from typing import List, Optional
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
    JobProfileExtractSkillsResponse
)
from src.services.file_processor import validate_file
from src.services.skills_extractor import extract_skills_from_text
from src.repository.crud.job_profile import JobProfileCRUDRepository

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

@router.delete(
    path="/job-profiles/{id}",
    name="job-profiles:delete",
    status_code=fastapi.status.HTTP_204_NO_CONTENT,
    summary="Delete a Job Profile",
)
async def delete_job_profile(
    id: int,
    current_user=fastapi.Depends(get_current_user),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
):
    success = await job_profile_repo.delete_profile(profile_id=id)
    if not success:
        raise fastapi.HTTPException(status_code=404, detail="Job Profile not found")
    return fastapi.Response(status_code=fastapi.status.HTTP_204_NO_CONTENT)

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


