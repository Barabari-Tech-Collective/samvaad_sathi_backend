import io
import re
import os
import time
import hashlib
from typing import Any, Dict, Tuple

import fastapi
import PyPDF2

def _verify_pdf_magic_bytes(raw_bytes: bytes) -> None:
    if not raw_bytes.startswith(b"%PDF"):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File content does not appear to be a valid PDF.",
        )

def _extract_text_from_pdf(raw_bytes: bytes) -> str:
    extracted_text = ""
    try:
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(raw_bytes))
        texts: list[str] = []
        if len(pdf_reader.pages) > 0:
            for page_num, page in enumerate(pdf_reader.pages):
                try:
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        texts.append(page_text)
                except Exception as page_error:
                    print(f"Warning: Failed to extract text from page {page_num + 1}: {page_error}")
                    continue
            extracted_text = "\n".join(texts)
            if not extracted_text.strip():
                try:
                    import pdfplumber
                    with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                        fallback_texts = []
                        for page in pdf.pages:
                            page_text = page.extract_text()
                            if page_text and page_text.strip():
                                fallback_texts.append(page_text)
                        extracted_text = "\n".join(fallback_texts)
                except ImportError:
                    print("Warning: pdfplumber not available for fallback PDF extraction")
                except Exception as fallback_error:
                    print(f"Warning: pdfplumber fallback failed: {fallback_error}")
    except Exception as pdf_error:
        print(f"PDF extraction error: {pdf_error}")
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                fallback_texts = []
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text and page_text.strip():
                        fallback_texts.append(page_text)
                extracted_text = "\n".join(fallback_texts)
        except Exception:
            extracted_text = ""
    return extracted_text

from src.api.dependencies.repository import get_repository
from src.repository.crud.user import UserCRUDRepository
from src.services.llm import extract_resume_entities_with_llm, extract_resume_entities_v2_with_llm

from src.api.dependencies.auth import get_current_user
from src.models.schemas.resume import ResumeExtractionResponse, MyResumeResponse, KnowledgeSetResponse


from src.services.s3_service import _background_upload_and_enforce_limit
from src.api.dependencies.session import get_async_session
from sqlalchemy.ext.asyncio import AsyncSession
import sqlalchemy
from src.models.db.user_resume import UserResume
router = fastapi.APIRouter(prefix="", tags=["resume"])


@router.post(
    path="/extract-resume",
    name="resume:extract-resume",
    response_model=ResumeExtractionResponse,
    status_code=fastapi.status.HTTP_202_ACCEPTED,
    summary="Upload a resume and extract skills/experience",
    description=(
        "Accepts a PDF or plain text file (<=5MB). Extracts text, calls an LLM to detect raw skills and years of "
        "experience, validates and normalizes results, and persists them to the authenticated user's profile."
    ),
)
async def extract_resume(
    background_tasks: fastapi.BackgroundTasks,
    file: fastapi.UploadFile = fastapi.File(
        ..., description="Resume file to upload. Allowed types: application/pdf, text/plain (max 5MB)"
    ),
    current_user=fastapi.Depends(get_current_user),
    user_repo: UserCRUDRepository = fastapi.Depends(get_repository(repo_type=UserCRUDRepository)),
):
    allowed_types = {"application/pdf", "text/plain"}
    content_type = file.content_type or ""

    if content_type not in allowed_types:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {content_type}",
        )

    # Enforce a max upload size of 5 MB while buffering content
    max_bytes = 5 * 1024 * 1024
    total_size = 0
    buffer = bytearray()
    chunk_size = 1024 * 64
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > max_bytes:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Uploaded file exceeds 5 MB limit",
            )
        buffer.extend(chunk)

    raw_bytes = bytes(buffer)

    if content_type == "application/pdf":
        _verify_pdf_magic_bytes(raw_bytes)

    # Extract text depending on the content type
    extracted_text = ""
    if content_type == "text/plain":
        extracted_text = raw_bytes.decode("utf-8", errors="ignore")
    elif content_type == "application/pdf":
        extracted_text = _extract_text_from_pdf(raw_bytes)

    # Normalize whitespace and control characters
    normalized = re.sub(r"\s+", " ", extracted_text or "").strip()
    preview = normalized[:300]

    # LLM extraction via service (v2 with fallback to v1)
    details: dict[str, Any] | None = None
    skills: list[str] = []
    years_experience: float | None = None
    llm_error: str | None = None
    llm_latency_ms: int | None = None
    llm_model: str | None = None

    data_v2, err_v2, latency_v2, model_v2 = await extract_resume_entities_v2_with_llm(normalized)
    if data_v2:
        details = data_v2
        skills = [str(s) for s in (data_v2.get("skills") or [])]
        years_experience = data_v2.get("years_experience")
        llm_error = err_v2
        llm_latency_ms = latency_v2
        llm_model = model_v2
    else:
        skills, years_experience, err_v1, latency_v1, model_v1 = await extract_resume_entities_with_llm(normalized)
        details = None
        llm_error = err_v1
        llm_latency_ms = latency_v1
        llm_model = model_v1

    # Validation & structuring
    warnings: list[str] = []

    def _norm_skill(s: str) -> str:
        s2 = re.sub(r"\s+", " ", s).strip().lower()
        return s2

    normalized_skills: list[str] = []
    seen: set[str] = set()
    for s in skills:
        s_str = _norm_skill(s)
        if not s_str:
            continue
        if not re.search(r"[a-z]", s_str):
            warnings.append(f"dropped skill without letters: '{s}'")
            continue
        if len(s_str) < 2 or len(s_str) > 64:
            warnings.append(f"dropped skill length out of range: '{s}'")
            continue
        if s_str not in seen:
            seen.add(s_str)
            normalized_skills.append(s_str)

    validated_years: float | None = None
    if years_experience is not None:
        try:
            validated_years = float(years_experience)
            if validated_years < 0:
                warnings.append("years_experience below 0; clamped to 0.0")
                validated_years = 0.0
            if validated_years > 50:
                warnings.append("years_experience above 50; clamped to 50.0")
                validated_years = 50.0
            validated_years = round(validated_years, 1)
        except Exception:
            validated_years = None
    else:
        # Try a simple fallback from text if LLM omitted
        m3 = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*years?", normalized, flags=re.I)
        if m3:
            try:
                v = float(m3.group(1))
                validated_years = max(0.0, min(50.0, round(v, 1)))
                warnings.append("years_experience inferred via regex fallback")
            except Exception:
                validated_years = None

    response = {
        "filename": file.filename,
        "content_type": content_type,
        "size": total_size,
        "text_length": len(normalized),
        "preview": preview,
        "skills": skills,
        "years_experience": years_experience,
        "llm_model": llm_model,
        "llm_latency_ms": llm_latency_ms,
        "llm_error": llm_error,
        "details": details,
        "validated": {
            "skills": normalized_skills,
            "years_experience": validated_years,
            "warnings": warnings,
        },
        "message": "File received and text extracted.",
    }

    # Persist validated data to User model
    try:
        # Try to persist additional profile hints if available
        degree_hint = None
        university_hint = None
        company_hint = None
        if details:
            # Prefer first education / experience entries if present
            edu_list = details.get("education") or []
            if isinstance(edu_list, list) and edu_list:
                first_edu = edu_list[0] or {}
                degree_hint = (first_edu.get("degree") or None)
                university_hint = (first_edu.get("institution") or None)
            exp_list = details.get("experience") or []
            if isinstance(exp_list, list) and exp_list:
                first_exp = exp_list[0] or {}
                company_hint = (first_exp.get("company") or None)

        await user_repo.update_resume_data(
            user_id=current_user.id,
            resume_text=normalized if normalized else None,
            years_experience=validated_years,
            skills=normalized_skills,
            degree=degree_hint,
            university=university_hint,
            company=company_hint,
        )
        response["saved"] = True
        
        import hashlib
        size_bytes = len(raw_bytes)
        file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        
        # Queue the background task to upload the original PDF to S3 only if DB update succeeds
        background_tasks.add_task(
            _background_upload_and_enforce_limit,
            raw_bytes,
            current_user.id,
            file.filename or "resume.pdf",
            content_type,
            normalized if normalized else None,
            "onboarding",
            size_bytes,
            file_sha256
        )
    except Exception as e:
        response["saved"] = False
        response["save_error"] = str(e)

    return response



@router.get(
    path="/me/resume",
    name="resume:get-my-resume",
    response_model=MyResumeResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Get my saved resume data",
    description="Returns only the authenticated user's saved resume fields and basic metadata.",
)
async def get_my_resume(
    current_user=fastapi.Depends(get_current_user),
):
    skills_field = current_user.skills if isinstance(current_user.skills, dict) else None
    items = skills_field.get("items", []) if skills_field else []
    if not isinstance(items, list):
        items = []

    return MyResumeResponse(
        user_id=current_user.id,
        email=current_user.email,
        years_experience=current_user.years_experience,
        skills=items,
        has_resume_text=bool(current_user.resume_text),
        text_length=len(current_user.resume_text or ""),
    )


@router.get(
    path="/get_knowledgeset",
    name="resume:get-knowledge-set",
    response_model=KnowledgeSetResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Derive a knowledge set from my resume",
    description=(
        "Uses the authenticated user's saved resume_text to produce a normalized skills list (knowledge set). "
        "Results are cached in-memory per user and resume content hash."
    ),
)
async def get_knowledge_set(
    current_user=fastapi.Depends(get_current_user),
):
    """
    Returns a knowledge set (normalized list of skills) derived from the authenticated user's resume.
    Access control: self-only (uses current_user from auth). Stateless operation for ECS compatibility.
    """
    # Use stored resume text if available
    resume_text = current_user.resume_text or ""
    normalized_text = re.sub(r"\s+", " ", resume_text).strip()

    if not normalized_text:
        return {
            "ok": False,
            "error": "No resume_text available for current user",
            "knowledge_set": {"items": []},
            "cached": False,
        }

    # Generate knowledge set directly (stateless - no caching for ECS compatibility)
    cached = False
    
    # Reuse LLM extraction service to get skills; ignore years here
    from src.services.llm import extract_resume_entities_with_llm  # local import to avoid cycles

    from src.services.llm import extract_resume_entities_with_llm as async_extract2
    skills, _years, llm_error, llm_latency_ms, llm_model = await async_extract2(normalized_text)

    # Normalize and validate
    def _norm_skill(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip().lower()

    normalized_skills: list[str] = []
    seen: set[str] = set()
    warnings: list[str] = []
    for s in skills or []:
        s_norm = _norm_skill(s)
        if not s_norm:
            continue
        if not re.search(r"[a-z]", s_norm):
            warnings.append(f"dropped skill without letters: '{s}'")
            continue
        if len(s_norm) < 2 or len(s_norm) > 64:
            warnings.append(f"dropped skill length out of range: '{s}'")
            continue
        if s_norm not in seen:
            seen.add(s_norm)
            normalized_skills.append(s_norm)

    result = {
        "knowledge_set": {"items": normalized_skills},
        "llm_model": llm_model,
        "llm_latency_ms": llm_latency_ms,
        "llm_error": llm_error,
        "warnings": warnings,
    }

    return {"ok": True, "cached": cached, **result}


@router.get(
    path="/download-original",
    name="resume:download-original",
    response_model=dict,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Download original resume",
    description="Returns a short-lived presigned URL to download the user's originally uploaded resume.",
)
async def download_original_resume(
    current_user=fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
):
    stmt = sqlalchemy.select(UserResume).where(
        UserResume.user_id == current_user.id,
        UserResume.source == "onboarding"
    ).order_by(UserResume.created_at.desc()).limit(1)
    result = await session.execute(stmt)
    latest_resume = result.scalar_one_or_none()
    
    if not latest_resume:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="No original resume found for this user."
        )
        
    from src.services.s3_service import get_presigned_url
    url = get_presigned_url(latest_resume.s3_key)
    
    if not url:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate download URL."
        )
        
    return {"url": url}

@router.post(
    path="/save-final-resume",
    name="resume:save-final-resume",
    response_model=dict,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Save a final ATS resume",
    description="Uploads a finalized resume from the ATS tool to S3 and enforces the max 3 resume limit.",
)
async def save_final_resume(
    file: fastapi.UploadFile = fastapi.File(
        ..., description="Resume file to upload. Allowed types: application/pdf (max 5MB)"
    ),
    current_user=fastapi.Depends(get_current_user),
    user_repo: UserCRUDRepository = fastapi.Depends(get_repository(repo_type=UserCRUDRepository)),
):
    if file.content_type not in ["application/pdf"]:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF files are supported for saving final resumes.",
        )

    # Enforce a max upload size of 5 MB while buffering content in chunks
    max_bytes = 5 * 1024 * 1024
    total_size = 0
    buffer = bytearray()
    chunk_size = 1024 * 64
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > max_bytes:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Uploaded file exceeds 5 MB limit",
            )
        buffer.extend(chunk)

    raw_bytes = bytes(buffer)

    if total_size == 0:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    # Verify PDF magic bytes
    _verify_pdf_magic_bytes(raw_bytes)

    import hashlib
    size_bytes = len(raw_bytes)
    file_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    # We no longer auto-update User.resume_text here. The user must explicitly set it via /set-active-resume.
    extracted_text = _extract_text_from_pdf(raw_bytes)
    normalized_text = re.sub(r"\s+", " ", extracted_text or "").strip()

    # Await the task directly to ensure it succeeds before returning 201
    try:
        s3_key = await _background_upload_and_enforce_limit(
            raw_bytes,
            current_user.id,
            file.filename or "ats_final.pdf",
            file.content_type,
            normalized_text if normalized_text else None,
            "ats_final",
            size_bytes,
            file_sha256
        )
        return {"message": "Final resume saved successfully.", "s3_key": s3_key}
    except Exception as e:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save resume. Please try again."
        )

from pydantic import BaseModel
class SetActiveResumeRequest(BaseModel):
    user_resume_id: int

@router.post(
    path="/set-active-resume",
    name="resume:set-active-resume",
    response_model=dict,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Set an explicitly saved resume as the active resume",
    description="Overrides the main profile resume (used for mock interviews) with a previously saved ATS resume.",
)
async def set_active_resume(
    request: SetActiveResumeRequest,
    current_user=fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
    user_repo: UserCRUDRepository = fastapi.Depends(get_repository(repo_type=UserCRUDRepository)),
):
    stmt = sqlalchemy.select(UserResume).where(
        UserResume.id == request.user_resume_id,
        UserResume.user_id == current_user.id
    )
    result = await session.execute(stmt)
    resume_record = result.scalar_one_or_none()
    
    if not resume_record:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Saved resume not found or does not belong to you."
        )
        
    if not resume_record.resume_text:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail="This saved resume has no extractable text."
        )
        
    await user_repo.update_only_resume_text(
        user_id=current_user.id, 
        resume_text=resume_record.resume_text
    )
    
    return {"message": "Successfully set as the active resume for mock interviews."}
