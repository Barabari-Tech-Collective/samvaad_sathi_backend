import uuid

import fastapi
from fastapi import (
    UploadFile,
    File,
    Form,
    Depends,
    HTTPException,
)
from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session

from src.models.db.user import User
from src.models.db.ai_resume_analysis import AIResumeAnalysis
from src.models.schemas.ai_resume import ResumeAnalysisResponse
from src.repository.crud.ai_resume_analysis import (
    AIResumeAnalysisCRUDRepository,
)
from src.services.ai_resume.parser_service import (
    extract_resume_text,
)

from src.services.ai_resume.ats_service import (
    generate_ats_analysis,
)

router = fastapi.APIRouter(
    prefix="/ai-resume",
    tags=["ai-resume"],
)


@router.post(
    "/analyze",
    status_code=200,
    summary="Analyze resume using ATS AI",
)
async def analyze_resume(
    resumeFile: UploadFile = File(...),
    targetRole: str = Form(...),
    experienceLevel: str = Form(...),
    jobDescription: str = Form(...),

    current_user: User = Depends(get_current_user),

    session: SQLAlchemyAsyncSession = Depends(
        get_async_session
    ),
):
    """
    Upload and analyze resume against job description.
    """

    try:
        # Validate fields
        if not targetRole.strip():
            raise fastapi.HTTPException(
                status_code=400,
                detail="Target role is required",
            )

        if not experienceLevel.strip():
            raise fastapi.HTTPException(
                status_code=400,
                detail="Experience level is required",
            )

        if not jobDescription.strip():
            raise fastapi.HTTPException(
                status_code=400,
                detail="Job description is required",
            )

        # Extract resume text
        extracted_text = await extract_resume_text(
            resumeFile
        )
        
        from src.services.ai_resume.url_validator import extract_professional_urls, validate_urls
        urls = extract_professional_urls(extracted_text)
        is_valid = await validate_urls(urls)
        
        if not is_valid:
            raise fastapi.HTTPException(
                status_code=400,
                detail="No proper working links found. Please ensure your resume contains valid GitHub, LinkedIn, or portfolio links."
            )

        # Generate ATS analysis
        analysis_result = await generate_ats_analysis(
            resume_text=extracted_text,
            target_role=targetRole,
            experience_level=experienceLevel,
            job_description=jobDescription,
        )

        # Create analysis ID
        analysis_id = str(uuid.uuid4())

        analysis_result["analysisId"] = analysis_id

        # Save in DB
        db_analysis = AIResumeAnalysis(
            analysis_id=analysis_id,
            user_id=current_user.id,
            target_role=targetRole,
            experience_level=experienceLevel,
            job_description=jobDescription,
            extracted_resume_text=extracted_text,
            analysis_result=analysis_result,
        )

        session.add(db_analysis)

        await session.commit()

        await session.refresh(db_analysis)

        # Return frontend-compatible response
        return analysis_result

    except fastapi.HTTPException:
        raise

    except Exception as e:
        await session.rollback()

        raise fastapi.HTTPException(
            status_code=500,
            detail=f"Resume analysis failed: {str(e)}",
        )


@router.get(
    "/analysis/{analysis_id}",
    response_model=ResumeAnalysisResponse,
    status_code=200,
)
async def get_resume_analysis(
    analysis_id: str,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    """
    Fetch already generated ATS analysis
    """

    repo = AIResumeAnalysisCRUDRepository(session)

    analysis = await repo.get_by_analysis_id(analysis_id)

    if not analysis:
        raise HTTPException(
            status_code=404,
            detail="Analysis not found",
        )

    # security check
    if analysis.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Access denied",
        )

    return analysis.analysis_result