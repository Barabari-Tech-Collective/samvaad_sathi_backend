"""Bulk ATS: screen many resumes against one role profile at once.

Backend-level RBAC (see get_current_bulk_ats_user) keeps this unavailable to
students regardless of UI - every route here depends on it, not on
get_current_user alone. This is screening *assistance*: nothing here
rejects or auto-advances a candidate: it only scores and flags for a human
reviewer (needs_review).
"""

import hashlib
import logging
import datetime
import secrets
from typing import Literal, Optional
from urllib.parse import quote, urlparse

import fastapi
import pydantic
from fastapi import UploadFile, File, Form, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession

from src.api.dependencies.admin import (
    get_current_admin_user,
    get_current_bulk_ats_user,
    is_admin_user,
    is_bulk_ats_user,
)
from src.api.dependencies.rate_limit import hybrid_rate_limiter
from src.api.dependencies.session import get_async_session
from src.config.manager import settings
from src.models.db.bulk_ats import BulkATSCandidate
from src.models.db.user import User
from src.repository.crud.bulk_ats import (
    BulkATSCRUDRepository,
    _feedback_request_is_active,
)
from src.repository.crud.user import UserCRUDRepository
from src.services.ai_resume.parser_service import ALLOWED_FILE_TYPES
from src.services.ai_resume.bulk.jd_quality import analyze_job_description
from src.services.ai_resume.bulk.malware_scan import scan_document
from src.services.ai_resume.bulk.google_drive_oauth import (
    build_authorization_request,
    configuration_errors as drive_oauth_configuration_errors,
    decode_state,
    encrypt_refresh_token,
    exchange_authorization_code,
    is_configured as drive_oauth_is_configured,
    revoke_refresh_token,
)
from src.services.s3_service import (
    upload_bulk_ats_resume_to_s3,
    delete_bulk_ats_resumes_from_s3,
)
from src.worker.queue import enqueue_job
from src.utilities.exceptions.database import EntityDoesNotExist

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(prefix="/bulk-ats", tags=["bulk-ats"])

_ALLOWED_EXCEL_CONTENT_TYPES = {
    "text/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

FeedbackAudience = Literal["student", "team"]


class GenerateFeedbackBody(pydantic.BaseModel):
    audience: FeedbackAudience
    regenerate: bool = False


class GenerateBatchFeedbackBody(GenerateFeedbackBody):
    candidate_ids: list[int] | None = pydantic.Field(default=None, max_length=100)


class MergeCandidateBody(pydantic.BaseModel):
    canonical_candidate_id: int


class UpdateRubricBody(pydantic.BaseModel):
    role_description: str = pydantic.Field(default="", max_length=12000)
    evaluation_intent: str = pydantic.Field(default="", max_length=2000)
    required_skills: list[str] = pydantic.Field(default_factory=list, max_length=100)
    preferred_skills: list[str] = pydantic.Field(default_factory=list, max_length=100)
    experience_mode: Literal["fresher", "experienced"] = "experienced"


def _drive_oauth_redirect(*, status: str, detail: str | None = None):
    base = settings.GOOGLE_DRIVE_OAUTH_FRONTEND_RETURN_URL or "/bulk-ats"
    separator = "&" if "?" in base else "?"
    target = f"{base}{separator}drive={quote(status)}"
    if detail:
        target += f"&reason={quote(detail[:240])}"
    response = fastapi.responses.RedirectResponse(target, status_code=303)
    response.delete_cookie(
        "bulk_ats_drive_oauth_state",
        path=f"{settings.API_PREFIX}/bulk-ats/google-drive",
    )
    return response


def _cell_text(value: object) -> str:
    """Normalize CSV/XLSX cells before validation.

    openpyxl returns numeric candidate IDs as ints/floats; calling ``strip``
    directly on those values used to abort the whole import.
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _parse_skills(value: str) -> list[str]:
    """Parse a recruiter-entered comma/newline list without duplicate noise."""
    skills: list[str] = []
    seen: set[str] = set()
    for raw_skill in value.replace("\n", ",").split(","):
        skill = raw_skill.strip()
        key = skill.casefold()
        if not skill or key in seen:
            continue
        if len(skill) > 100:
            raise HTTPException(
                status_code=400, detail="Each skill must be at most 100 characters"
            )
        seen.add(key)
        skills.append(skill)
    if len(skills) > 100:
        raise HTTPException(
            status_code=400, detail="At most 100 skills may be supplied"
        )
    return skills


def _parse_resume_urls(value: str) -> list[dict]:
    """Convert a newline-separated paste into the existing import row shape."""
    rows: list[dict] = []
    for line in value.splitlines():
        resume_url = line.strip()
        if resume_url:
            rows.append({"resume_url": resume_url, "source": "pasted_link"})
    return rows


def _require_role_rubric(
    *,
    job_profile_id: int | None,
    role_description: str,
    required_skills: list[str],
) -> None:
    """Reject batches that cannot produce an evidence-based role-fit score."""
    if job_profile_id is None and len(role_description) < 50 and not required_skills:
        raise HTTPException(
            status_code=400,
            detail=(
                "Add a job description of at least 50 characters or at least "
                "one required skill so candidates can be evaluated against a "
                "real role rubric"
            ),
        )


async def _mark_queue_failure(
    repo: BulkATSCRUDRepository,
    *,
    candidate_id: int,
    batch_id: int,
) -> None:
    await repo.update_candidate_status(
        candidate_id,
        "failed",
        error_message="Background processing is unavailable. Rerun this candidate after the worker recovers.",
    )
    await repo.refresh_batch_status(batch_id)


def _feedback_states(report: dict | None) -> dict:
    shareable = (report or {}).get("shareableFeedback") or {}
    if not isinstance(shareable, dict):
        shareable = {}
    states: dict[str, str | None] = {}
    for audience in ("student", "team"):
        state = shareable.get(audience)
        if not isinstance(state, dict):
            states[audience] = None
            continue
        status = state.get("status")
        if status in {"queued", "generating"} and not _feedback_request_is_active(
            state
        ):
            # Do not make clients poll forever when a worker stopped without
            # writing a terminal state. A new request can safely reserve this
            # stale artifact because begin_feedback_request uses the same rule.
            states[audience] = "failed"
        else:
            states[audience] = status
    return states


def _format_feedback_copy(
    *,
    candidate: BulkATSCandidate,
    audience: FeedbackAudience,
    role_name: str,
    content: dict,
) -> str:
    """Create a paste-ready artifact without asking the model for PII."""

    name = candidate.candidate_name or "Candidate"
    lines = [name, f"Resume feedback for: {role_name}"]
    if audience == "team" and candidate.final_score is not None:
        lines.append(f"ATS score: {candidate.final_score:.2f}/100")
    lines.extend(["", str(content.get("summary") or "").strip()])
    sections = (
        ("Strengths", content.get("strengths") or []),
        (
            "Improvements" if audience == "student" else "Gaps to validate",
            content.get("improvements") or [],
        ),
        ("Next steps", content.get("nextSteps") or []),
        ("Interview focus", content.get("interviewFocus") or []),
    )
    for heading, items in sections:
        if not isinstance(items, list):
            continue
        clean_items = [str(item).strip() for item in items if str(item).strip()]
        if not clean_items:
            continue
        lines.extend(["", f"{heading}:"])
        lines.extend(f"- {item}" for item in clean_items)
    return "\n".join(line for line in lines if line is not None).strip()


def _candidate_summary(c: BulkATSCandidate) -> dict:
    """Compact row for the bulk table - no long-form feedback, per spec."""
    return {
        "id": c.id,
        "candidateName": c.candidate_name,
        "emailOrCandidateId": c.email_or_candidate_id,
        "finalScore": c.final_score,
        "scoreBand": c.score_band,
        "status": c.status,
        "needsReview": c.needs_review,
        "warnings": c.warnings or [],
        "confidenceScore": c.confidence_score,
        "scoreLowerBound": c.score_lower_bound,
        "scoreUpperBound": c.score_upper_bound,
        "reviewerDecision": c.reviewer_decision,
        "interviewOutcome": c.interview_outcome,
        "assignedReviewerId": c.assigned_reviewer_id,
        "feedbackStatus": _feedback_states(c.deterministic_report),
    }


def _candidate_detail(c: BulkATSCandidate) -> dict:
    parsed_resume = c.parsed_resume_json or {}
    raw_skills = (
        parsed_resume.get("skills", []) if isinstance(parsed_resume, dict) else []
    )
    extracted_skills = [
        skill.strip()
        for skill in raw_skills
        if isinstance(skill, str) and skill.strip()
    ]
    return {
        **_candidate_summary(c),
        "originalFilename": c.original_filename,
        "resumeUrl": c.resume_url,
        "roleName": c.batch.role_name if c.batch is not None else None,
        "errorMessage": c.error_message,
        "extractedSkills": extracted_skills,
        "deterministicReport": c.deterministic_report,
        "jevReport": c.jev_report,
        "linkReport": c.link_report,
        "reviewerNotes": c.reviewer_notes,
        "decisionReason": c.decision_reason,
        "outcomeRecordedAt": c.outcome_recorded_at,
        "assignedReviewerId": c.assigned_reviewer_id,
        "createdAt": c.created_at,
        "updatedAt": c.updated_at,
    }


async def _get_authorized_batch(
    repo: BulkATSCRUDRepository,
    *,
    batch_id: int,
    current_user: User,
):
    batch = await repo.get_batch(batch_id)
    if batch is None or (
        not is_admin_user(current_user) and batch.created_by != current_user.id
    ):
        # Do not reveal that another recruiter's batch exists.
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


async def _get_authorized_candidate(
    repo: BulkATSCRUDRepository,
    *,
    candidate_id: int,
    current_user: User,
) -> BulkATSCandidate:
    candidate = await repo.get_candidate(candidate_id)
    if candidate is None or (
        not is_admin_user(current_user)
        and (candidate.batch is None or candidate.batch.created_by != current_user.id)
    ):
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


def _require_deepseek_feedback() -> None:
    if not settings.BULK_ATS_DEEPSEEK_FEEDBACK_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Shareable feedback is currently disabled",
        )
    if not settings.DEEPSEEK_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="DeepSeek is not configured for shareable feedback",
        )


async def _queue_candidate_feedback(
    *,
    repo: BulkATSCRUDRepository,
    candidate: BulkATSCandidate,
    audience: FeedbackAudience,
    regenerate: bool,
    requested_by: int,
) -> dict:
    if candidate.status not in {"completed", "needs_review"}:
        return {"candidateId": candidate.id, "status": "not_eligible"}

    report = candidate.deterministic_report or {}
    if not report.get("bulkAtsScorecard"):
        return {"candidateId": candidate.id, "status": "not_eligible"}

    request_id = secrets.token_urlsafe(18)
    requested_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    queued_state = {
        "status": "queued",
        "audience": audience,
        "requestId": request_id,
        "requestedAt": requested_at,
        "requestedBy": requested_by,
    }
    reserved_candidate, reservation_status = await repo.begin_feedback_request(
        candidate.id,
        audience=audience,
        state=queued_state,
        regenerate=regenerate,
    )
    if reservation_status != "reserved":
        return {
            "candidateId": candidate.id,
            "status": (
                reservation_status
                if reservation_status in {"already_in_progress", "already_ready"}
                else "not_eligible"
            ),
        }
    candidate = reserved_candidate or candidate

    job_id = await enqueue_job(
        "generate_bulk_ats_feedback_task",
        candidate_id=candidate.id,
        audience=audience,
        request_id=request_id,
        requested_by=requested_by,
    )
    if not job_id:
        failed_state = {
            **queued_state,
            "status": "failed",
            "error": "Feedback queue is temporarily unavailable. Please try again.",
            "completedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        await repo.update_feedback_state(
            candidate.id,
            audience=audience,
            state=failed_state,
            expected_request_id=request_id,
        )
        status = "queue_unavailable"
    else:
        status = "queued"

    await repo.record_audit(
        action="candidate_feedback_requested",
        actor_id=requested_by,
        batch_id=candidate.batch_id,
        candidate_id=candidate.id,
        metadata={
            "audience": audience,
            "requestId": request_id,
            "regenerate": regenerate,
            "queued": bool(job_id),
        },
    )
    return {
        "candidateId": candidate.id,
        "status": status,
        "jobId": job_id,
    }


@router.post("/batches", status_code=201)
async def create_batch(
    name: str = Form(..., max_length=160),
    role_name: str = Form(..., max_length=160),
    role_description: str = Form("", max_length=12000),
    evaluation_intent: str = Form("", max_length=2000),
    required_skills: str = Form("", max_length=10000),
    preferred_skills: str = Form("", max_length=10000),
    experience_mode: str = Form("experienced"),
    retention_days: int = Form(settings.BULK_ATS_DEFAULT_RETENTION_DAYS),
    job_profile_id: Optional[int] = Form(None),
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    if experience_mode not in ("fresher", "experienced"):
        raise HTTPException(
            status_code=400, detail="experience_mode must be 'fresher' or 'experienced'"
        )
    if not name.strip() or not role_name.strip():
        raise HTTPException(status_code=400, detail="name and role_name are required")
    parsed_required_skills = _parse_skills(required_skills)
    parsed_preferred_skills = _parse_skills(preferred_skills)
    normalized_role_description = role_description.strip()
    _require_role_rubric(
        job_profile_id=job_profile_id,
        role_description=normalized_role_description,
        required_skills=parsed_required_skills,
    )
    if retention_days < 1 or retention_days > settings.BULK_ATS_MAX_RETENTION_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"retention_days must be between 1 and {settings.BULK_ATS_MAX_RETENTION_DAYS}",
        )
    jd_analysis = analyze_job_description(
        normalized_role_description,
        required_skills=parsed_required_skills,
        preferred_skills=parsed_preferred_skills,
    )
    normalized_intent = evaluation_intent.strip()
    if normalized_intent:
        intent_analysis = analyze_job_description(
            normalized_intent,
            required_skills=parsed_required_skills,
            preferred_skills=parsed_preferred_skills,
        )
        jd_analysis["evaluationIntent"] = intent_analysis
        if intent_analysis["riskyCriteria"]:
            jd_analysis["riskyCriteria"] = sorted(
                set(jd_analysis["riskyCriteria"] + intent_analysis["riskyCriteria"])
            )
            jd_analysis["warnings"] = sorted(
                set(
                    jd_analysis["warnings"]
                    + ["evaluation_intent_has_potentially_discriminatory_criteria"]
                )
            )
    retention_until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        days=retention_days
    )

    repo = BulkATSCRUDRepository(session)
    batch = await repo.create_batch(
        name=name.strip(),
        role_name=role_name.strip(),
        role_description=normalized_role_description or None,
        evaluation_intent=normalized_intent or None,
        required_skills=parsed_required_skills or None,
        preferred_skills=parsed_preferred_skills or None,
        experience_mode=experience_mode,
        job_profile_id=job_profile_id,
        weight_overrides=None,
        jd_analysis=jd_analysis,
        retention_until=retention_until,
        created_by=current_user.id,
        rubric_snapshot={
            "roleName": role_name.strip(),
            "roleDescription": normalized_role_description or None,
            "evaluationIntent": normalized_intent or None,
            "requiredSkills": parsed_required_skills,
            "preferredSkills": parsed_preferred_skills,
            "experienceMode": experience_mode,
            "jdAnalysis": jd_analysis,
        },
    )
    await repo.record_audit(
        action="batch_created",
        actor_id=current_user.id,
        batch_id=batch.id,
        metadata={
            "retentionDays": retention_days,
            "jdQuality": jd_analysis["level"],
            "hasEvaluationIntent": bool(normalized_intent),
        },
    )
    return {
        "id": batch.id,
        "name": batch.name,
        "roleName": batch.role_name,
        "status": batch.status,
        "createdAt": batch.created_at,
        "jdAnalysis": batch.jd_analysis,
        "rubricVersion": batch.rubric_version,
        "rubricSnapshot": batch.rubric_snapshot,
        "retentionUntil": batch.retention_until,
    }


@router.get("/batches")
async def list_batches(
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    limit: int = 50,
    offset: int = 0,
    assigned_reviewer_id: int | None = None,
):
    repo = BulkATSCRUDRepository(session)
    batches = await repo.list_batches(
        created_by=None if is_admin_user(current_user) else current_user.id,
        limit=min(limit, 200),
        offset=offset,
    )
    # Self-heal stale processing states left by an interrupted worker after all
    # candidate rows had already reached a terminal state.
    for batch in batches:
        if batch.status == "processing":
            await repo.refresh_batch_status(batch.id)
    return [
        {
            "id": b.id,
            "name": b.name,
            "roleName": b.role_name,
            "status": b.status,
            "createdAt": b.created_at,
            "createdBy": {
                "id": b.created_by,
                "name": b.creator.name if b.creator else None,
                "email": b.creator.email if b.creator else None,
            },
        }
        for b in batches
    ]


@router.get("/batches/{batch_id}")
async def get_batch(
    batch_id: int,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    repo = BulkATSCRUDRepository(session)
    batch = await _get_authorized_batch(
        repo, batch_id=batch_id, current_user=current_user
    )
    await repo.refresh_batch_status(batch_id)

    total = await repo.count_candidates(batch_id=batch_id)
    completed = await repo.count_candidates(batch_id=batch_id, status="completed")
    needs_review = await repo.count_candidates(batch_id=batch_id, status="needs_review")
    failed = await repo.count_candidates(batch_id=batch_id, status="failed")

    return {
        "id": batch.id,
        "name": batch.name,
        "roleName": batch.role_name,
        "roleDescription": batch.role_description,
        "evaluationIntent": batch.evaluation_intent,
        "requiredSkills": batch.required_skills or [],
        "preferredSkills": batch.preferred_skills or [],
        "experienceMode": batch.experience_mode,
        "jdAnalysis": batch.jd_analysis,
        "rubricVersion": batch.rubric_version,
        "rubricSnapshot": batch.rubric_snapshot,
        "retentionUntil": batch.retention_until,
        "status": batch.status,
        "createdAt": batch.created_at,
        "createdBy": {
            "id": batch.created_by,
            "name": batch.creator.name if batch.creator else None,
            "email": batch.creator.email if batch.creator else None,
        },
        "counts": {
            "total": total,
            "completed": completed,
            "needsReview": needs_review,
            "failed": failed,
            "pending": total - completed - needs_review - failed,
        },
    }


@router.delete("/batches/{batch_id}", status_code=204)
async def delete_batch(
    batch_id: int,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    repo = BulkATSCRUDRepository(session)
    batch = await _get_authorized_batch(
        repo, batch_id=batch_id, current_user=current_user
    )
    candidates = await repo.list_candidates(
        batch_id=batch_id, limit=1000, sort="created"
    )
    deletion = await delete_bulk_ats_resumes_from_s3(
        [c.s3_key for c in candidates if c.s3_key]
    )
    if deletion["errors"]:
        raise HTTPException(
            status_code=503,
            detail="Stored resumes could not be deleted; the batch was retained",
        )
    await repo.record_audit(
        action="batch_deleted",
        actor_id=current_user.id,
        batch_id=batch.id,
        metadata={"storedResumesDeleted": deletion["deleted"]},
    )
    await repo.delete_batch(batch)


@router.post("/batches/{batch_id}/resumes", status_code=202)
async def upload_resumes(
    batch_id: int,
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    _rate_limit=Depends(
        hybrid_rate_limiter(
            key_prefix="bulk_ats_upload",
            limit=settings.RATE_LIMIT_BULK_ATS_UPLOAD_PER_HOUR,
            window_seconds=3600,
        )
    ),
):
    """Accepts a PDF/DOCX folder-style multi-file upload for one batch.
    Each file becomes one candidate row and is queued for scoring
    independently - one bad file never blocks the rest of the upload."""
    repo = BulkATSCRUDRepository(session)
    batch = await _get_authorized_batch(
        repo, batch_id=batch_id, current_user=current_user
    )

    existing_count = await repo.count_candidates(batch_id=batch_id)
    remaining_capacity = settings.BULK_ATS_MAX_FILES_PER_BATCH - existing_count
    if len(files) > remaining_capacity:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This batch has room for {max(remaining_capacity, 0)} more "
                f"candidate(s); the batch limit is "
                f"{settings.BULK_ATS_MAX_FILES_PER_BATCH}"
            ),
        )
    if len(files) > settings.BULK_ATS_MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"At most {settings.BULK_ATS_MAX_FILES_PER_BATCH} files per upload",
        )

    accepted: list[dict] = []
    skipped: list[dict] = []

    for upload in files:
        try:
            if upload.content_type not in ALLOWED_FILE_TYPES:
                skipped.append(
                    {"filename": upload.filename, "reason": "unsupported file type"}
                )
                continue

            raw_bytes = await upload.read()
            if not raw_bytes:
                skipped.append({"filename": upload.filename, "reason": "empty file"})
                continue
            if len(raw_bytes) > settings.BULK_ATS_MAX_FILE_SIZE_MB * 1024 * 1024:
                skipped.append(
                    {"filename": upload.filename, "reason": "file too large"}
                )
                continue

            malware = await scan_document(raw_bytes)
            if malware["status"] in {"infected", "error"}:
                skipped.append(
                    {
                        "filename": upload.filename,
                        "reason": "document failed malware safety scan",
                    }
                )
                continue

            sha256 = hashlib.sha256(raw_bytes).hexdigest()
            duplicate = await repo.find_duplicate(
                batch_id=batch_id, file_sha256=sha256, candidate_key=None
            )
            if duplicate is not None:
                skipped.append(
                    {
                        "filename": upload.filename,
                        "reason": "duplicate of an existing candidate in this batch",
                    }
                )
                continue

            s3_key = upload_bulk_ats_resume_to_s3(
                raw_bytes,
                batch_id,
                upload.filename or "resume.pdf",
                upload.content_type,
            )
            if s3_key is None:
                skipped.append(
                    {"filename": upload.filename, "reason": "storage upload failed"}
                )
                continue

            candidate = BulkATSCandidate(
                batch_id=batch_id,
                original_filename=upload.filename,
                file_sha256=sha256,
                s3_key=s3_key,
                source="upload",
                status="queued",
            )
            candidate = await repo.create_candidate(candidate)
            queued = await enqueue_job(
                "process_bulk_ats_candidate_task", candidate_id=candidate.id
            )
            if not queued:
                await _mark_queue_failure(
                    repo, candidate_id=candidate.id, batch_id=batch_id
                )
            accepted.append(
                {
                    "candidateId": candidate.id,
                    "filename": upload.filename,
                    "queued": bool(queued),
                }
            )
        except Exception as exc:
            logger.exception("Bulk ATS upload failed for file %s", upload.filename)
            skipped.append({"filename": upload.filename, "reason": str(exc)})

    await repo.refresh_batch_status(batch_id)
    await repo.record_audit(
        action="resumes_uploaded",
        actor_id=current_user.id,
        batch_id=batch_id,
        metadata={"accepted": len(accepted), "skipped": len(skipped)},
    )
    return {"accepted": accepted, "skipped": skipped}


@router.post("/batches/{batch_id}/import-links", status_code=202)
async def import_links(
    batch_id: int,
    file: UploadFile | None = File(None),
    resume_urls: str = Form("", max_length=210000),
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    _rate_limit=Depends(
        hybrid_rate_limiter(
            key_prefix="bulk_ats_import",
            limit=settings.RATE_LIMIT_BULK_ATS_UPLOAD_PER_HOUR,
            window_seconds=3600,
        )
    ),
):
    """Imports candidates from an Excel/CSV sheet with columns
    candidate_name, email_or_candidate_id, resume_url, role, source. The
    actual resume_url fetch happens in the worker task (see
    process_bulk_ats_candidate_task), not here, so one slow/dead link can't
    hold up the request."""
    if file is None and not resume_urls.strip():
        raise HTTPException(
            status_code=400,
            detail="Upload a CSV/XLSX file or paste at least one resume URL",
        )
    if file is not None and resume_urls.strip():
        raise HTTPException(
            status_code=400,
            detail="Choose either a spreadsheet or pasted resume URLs, not both",
        )
    if file is not None and file.content_type not in _ALLOWED_EXCEL_CONTENT_TYPES:
        raise HTTPException(
            status_code=400, detail="Only CSV or Excel (.xlsx) files are supported"
        )

    repo = BulkATSCRUDRepository(session)
    batch = await _get_authorized_batch(
        repo, batch_id=batch_id, current_user=current_user
    )

    rows = (
        await _read_candidate_rows(file)
        if file is not None
        else _parse_resume_urls(resume_urls)
    )
    existing_count = await repo.count_candidates(batch_id=batch_id)
    remaining_capacity = settings.BULK_ATS_MAX_FILES_PER_BATCH - existing_count
    if len(rows) > remaining_capacity:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This batch has room for {max(remaining_capacity, 0)} more "
                f"candidate(s); the batch limit is "
                f"{settings.BULK_ATS_MAX_FILES_PER_BATCH}"
            ),
        )

    accepted: list[dict] = []
    skipped: list[dict] = []

    for row in rows:
        resume_url = _cell_text(row.get("resume_url"))
        email_or_id = _cell_text(row.get("email_or_candidate_id"))
        candidate_name = _cell_text(row.get("candidate_name"))
        source = _cell_text(row.get("source")) or "excel_import"
        if not resume_url:
            skipped.append({"row": row, "reason": "missing resume_url"})
            continue
        if urlparse(resume_url).scheme.lower() not in {"http", "https"}:
            skipped.append({"row": row, "reason": "resume_url must use http or https"})
            continue
        if len(resume_url) > 2048 or len(email_or_id) > 256:
            skipped.append(
                {"row": row, "reason": "candidate ID or resume URL is too long"}
            )
            continue

        dedupe_value = (
            f"{email_or_id.lower()}|{resume_url.lower()}"
            if email_or_id
            else resume_url.lower()
        )
        candidate_key = hashlib.sha256(dedupe_value.encode("utf-8")).hexdigest()
        duplicate = await repo.find_duplicate(
            batch_id=batch_id, file_sha256=None, candidate_key=candidate_key
        )
        if duplicate is not None:
            skipped.append({"row": row, "reason": "duplicate candidate in this batch"})
            continue

        candidate = BulkATSCandidate(
            batch_id=batch_id,
            candidate_name=candidate_name[:256] or None,
            email_or_candidate_id=email_or_id or None,
            candidate_key=candidate_key,
            resume_url=resume_url,
            original_filename=resume_url.rsplit("/", 1)[-1][:256],
            source=source[:64],
            status="queued",
        )
        candidate = await repo.create_candidate(candidate)
        queued = await enqueue_job(
            "process_bulk_ats_candidate_task", candidate_id=candidate.id
        )
        if not queued:
            await _mark_queue_failure(
                repo, candidate_id=candidate.id, batch_id=batch_id
            )
        accepted.append({"candidateId": candidate.id, "queued": bool(queued)})

    await repo.refresh_batch_status(batch_id)
    await repo.record_audit(
        action="resume_links_imported",
        actor_id=current_user.id,
        batch_id=batch_id,
        metadata={"accepted": len(accepted), "skipped": len(skipped)},
    )
    return {"accepted": accepted, "skipped": skipped}


async def _read_candidate_rows(file: UploadFile) -> list[dict]:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Candidate import file is empty")
    if len(content) > settings.BULK_ATS_MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=400, detail="Candidate import file is too large"
        )
    filename = (file.filename or "").lower()

    if filename.endswith(".csv") or file.content_type == "text/csv":
        import csv
        import io

        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames:
            reader.fieldnames = [
                (name or "").strip().lower() for name in reader.fieldnames
            ]
        return [dict(row) for row in reader]

    import io

    import openpyxl

    workbook = openpyxl.load_workbook(
        io.BytesIO(content), read_only=True, data_only=True
    )
    sheet = workbook.active
    rows_iter = sheet.iter_rows(values_only=True)
    try:
        header = [
            str(h).strip().lower() if h is not None else "" for h in next(rows_iter)
        ]
    except StopIteration:
        return []

    results = []
    for row in rows_iter:
        if row is None or all(v is None for v in row):
            continue
        results.append(
            {header[i]: (row[i] if i < len(row) else None) for i in range(len(header))}
        )
    return results


@router.get("/batches/{batch_id}/candidates")
async def list_candidates(
    batch_id: int,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    status: Optional[str] = None,
    sort: Literal["score", "confidence", "review_priority", "created"] = "score",
    limit: int = 50,
    offset: int = 0,
):
    repo = BulkATSCRUDRepository(session)
    batch = await _get_authorized_batch(
        repo, batch_id=batch_id, current_user=current_user
    )

    candidates = await repo.list_candidates(
        batch_id=batch_id,
        status=status,
        sort=sort,
        limit=min(limit, 200),
        offset=offset,
        assigned_reviewer_id=assigned_reviewer_id,
    )
    total = await repo.count_candidates(
        batch_id=batch_id,
        status=status,
        assigned_reviewer_id=assigned_reviewer_id,
    )
    return {"total": total, "candidates": [_candidate_summary(c) for c in candidates]}


@router.get("/candidates/{candidate_id}")
async def get_candidate(
    candidate_id: int,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    repo = BulkATSCRUDRepository(session)
    candidate = await _get_authorized_candidate(
        repo, candidate_id=candidate_id, current_user=current_user
    )
    batch_candidates = await repo.list_candidates(batch_id=candidate.batch_id, limit=1000, sort="score")
    scored = [item for item in batch_candidates if item.final_score is not None]
    scores = sorted(item.final_score for item in scored if item.final_score is not None)
    rank = next((index + 1 for index, item in enumerate(scored) if item.id == candidate.id), None)
    score = candidate.final_score
    percentile = None
    median = None
    if scores and score is not None:
        midpoint = len(scores) // 2
        median = scores[midpoint] if len(scores) % 2 else (scores[midpoint - 1] + scores[midpoint]) / 2
        percentile = round(sum(value <= score for value in scores) / len(scores) * 100, 1)
    detail = _candidate_detail(candidate)
    detail["rankExplanation"] = {
        "rank": rank,
        "totalScored": len(scores),
        "percentile": percentile,
        "batchMedian": median,
        "strongestEvidence": ((candidate.deterministic_report or {}).get("deterministicMetrics", {}).get("projectModule", {}).get("projectEvaluation", [])[:1]),
        "missingEvidence": candidate.warnings or [],
    }
    return detail


@router.post("/candidates/{candidate_id}/merge")
async def merge_candidate(
    candidate_id: int,
    payload: MergeCandidateBody,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    repo = BulkATSCRUDRepository(session)
    duplicate = await _get_authorized_candidate(repo, candidate_id=candidate_id, current_user=current_user)
    canonical = await _get_authorized_candidate(repo, candidate_id=payload.canonical_candidate_id, current_user=current_user)
    if duplicate.batch_id != canonical.batch_id:
        raise HTTPException(status_code=400, detail="Candidates must belong to the same batch")
    merged, archived = await repo.merge_candidates(duplicate_id=duplicate.id, canonical_id=canonical.id)
    if merged is None or archived is None:
        raise HTTPException(status_code=400, detail="Candidates could not be merged")
    await repo.record_audit(
        action="candidate_merged",
        actor_id=current_user.id,
        batch_id=merged.batch_id,
        candidate_id=merged.id,
        metadata={"mergedCandidateId": archived.id, "canonicalCandidateId": merged.id},
    )
    return {"canonicalCandidateId": merged.id, "mergedCandidateId": archived.id}


@router.post("/batches/{batch_id}/rubric-versions")
async def create_rubric_version(
    batch_id: int,
    payload: UpdateRubricBody,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    repo = BulkATSCRUDRepository(session)
    batch = await _get_authorized_batch(repo, batch_id=batch_id, current_user=current_user)
    required = [skill.strip() for skill in payload.required_skills if skill.strip()]
    preferred = [skill.strip() for skill in payload.preferred_skills if skill.strip()]
    description = payload.role_description.strip()
    _require_role_rubric(job_profile_id=batch.job_profile_id, role_description=description, required_skills=required)
    next_version = (batch.rubric_version or 1) + 1
    analysis = analyze_job_description(description, required_skills=required, preferred_skills=preferred)
    batch.rubric_version = next_version
    batch.role_description = description or None
    batch.evaluation_intent = payload.evaluation_intent.strip() or None
    batch.required_skills = required or None
    batch.preferred_skills = preferred or None
    batch.experience_mode = payload.experience_mode
    batch.jd_analysis = analysis
    batch.rubric_snapshot = {**payload.model_dump(), "version": next_version, "jdAnalysis": analysis}
    await session.commit()
    candidates = await repo.list_candidates(batch_id=batch_id, limit=1000)
    queued = 0
    for candidate in candidates:
        if await enqueue_job("process_bulk_ats_candidate_task", candidate_id=candidate.id):
            queued += 1
    await repo.record_audit(action="rubric_version_created", actor_id=current_user.id, batch_id=batch_id, metadata={"version": next_version, "queued": queued})
    return {"batchId": batch_id, "rubricVersion": next_version, "queued": queued}


@router.post("/candidates/{candidate_id}/feedback", status_code=202)
async def generate_candidate_feedback(
    candidate_id: int,
    payload: GenerateFeedbackBody,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    _rate_limit=Depends(
        hybrid_rate_limiter(
            key_prefix="bulk_ats_feedback",
            limit=settings.RATE_LIMIT_BULK_ATS_FEEDBACK_PER_HOUR,
            window_seconds=3600,
        )
    ),
):
    """Queue one explicit, auditable DeepSeek feedback artifact."""

    _require_deepseek_feedback()
    repo = BulkATSCRUDRepository(session)
    candidate = await _get_authorized_candidate(
        repo, candidate_id=candidate_id, current_user=current_user
    )
    result = await _queue_candidate_feedback(
        repo=repo,
        candidate=candidate,
        audience=payload.audience,
        regenerate=payload.regenerate,
        requested_by=current_user.id,
    )
    if result["status"] == "not_eligible":
        raise HTTPException(
            status_code=409,
            detail="Complete candidate screening before generating feedback",
        )
    if result["status"] == "queue_unavailable":
        raise HTTPException(
            status_code=503,
            detail="Feedback queue is temporarily unavailable. Please try again.",
        )
    return result


@router.post("/batches/{batch_id}/feedback", status_code=202)
async def generate_batch_feedback(
    batch_id: int,
    payload: GenerateBatchFeedbackBody,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    _rate_limit=Depends(
        hybrid_rate_limiter(
            key_prefix="bulk_ats_feedback",
            limit=settings.RATE_LIMIT_BULK_ATS_FEEDBACK_PER_HOUR,
            window_seconds=3600,
        )
    ),
):
    """Queue feedback for a recruiter-selected set or every eligible candidate."""

    _require_deepseek_feedback()
    repo = BulkATSCRUDRepository(session)
    await _get_authorized_batch(repo, batch_id=batch_id, current_user=current_user)
    candidates = await repo.list_candidates(
        batch_id=batch_id, limit=settings.BULK_ATS_MAX_FILES_PER_BATCH, sort="created"
    )

    requested_ids = set(payload.candidate_ids or [])
    if payload.candidate_ids is not None and not requested_ids:
        raise HTTPException(status_code=400, detail="Select at least one candidate")
    if requested_ids:
        found_ids = {candidate.id for candidate in candidates}
        if requested_ids - found_ids:
            raise HTTPException(
                status_code=400,
                detail="One or more selected candidates are not in this batch",
            )
        candidates = [
            candidate for candidate in candidates if candidate.id in requested_ids
        ]

    results = []
    for candidate in candidates:
        results.append(
            await _queue_candidate_feedback(
                repo=repo,
                candidate=candidate,
                audience=payload.audience,
                regenerate=payload.regenerate,
                requested_by=current_user.id,
            )
        )

    counts: dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    await repo.record_audit(
        action="batch_feedback_requested",
        actor_id=current_user.id,
        batch_id=batch_id,
        metadata={
            "audience": payload.audience,
            "regenerate": payload.regenerate,
            "counts": counts,
        },
    )
    return {
        "audience": payload.audience,
        "counts": counts,
        "results": results,
    }


@router.get("/batches/{batch_id}/feedback")
async def list_batch_feedback(
    batch_id: int,
    audience: FeedbackAudience,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    """Return only completed artifacts for explicit copy/export actions."""

    repo = BulkATSCRUDRepository(session)
    batch = await _get_authorized_batch(
        repo, batch_id=batch_id, current_user=current_user
    )
    candidates = await repo.list_candidates(
        batch_id=batch_id, limit=settings.BULK_ATS_MAX_FILES_PER_BATCH, sort="created"
    )
    items = []
    for candidate in candidates:
        state = (
            (candidate.deterministic_report or {}).get("shareableFeedback") or {}
        ).get(audience) or {}
        content = state.get("content")
        if state.get("status") != "ready" or not isinstance(content, dict):
            continue
        items.append(
            {
                "candidateId": candidate.id,
                "candidateName": candidate.candidate_name,
                "emailOrCandidateId": candidate.email_or_candidate_id,
                "audience": audience,
                "generatedAt": (state.get("meta") or {}).get("generatedAt"),
                "content": content,
                "copyText": _format_feedback_copy(
                    candidate=candidate,
                    audience=audience,
                    role_name=batch.role_name,
                    content=content,
                ),
            }
        )
    return {"audience": audience, "total": len(items), "items": items}


@router.post("/candidates/{candidate_id}/rerun", status_code=202)
async def rerun_candidate(
    candidate_id: int,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    repo = BulkATSCRUDRepository(session)
    candidate = await _get_authorized_candidate(
        repo, candidate_id=candidate_id, current_user=current_user
    )

    await repo.update_candidate_status(candidate_id, "queued", error_message=None)
    queued = await enqueue_job(
        "process_bulk_ats_candidate_task", candidate_id=candidate_id
    )
    if not queued:
        await _mark_queue_failure(
            repo, candidate_id=candidate_id, batch_id=candidate.batch_id
        )
        await repo.record_audit(
            action="candidate_rerun_requested",
            actor_id=current_user.id,
            batch_id=candidate.batch_id,
            candidate_id=candidate.id,
            metadata={"queued": False},
        )
        raise HTTPException(
            status_code=503,
            detail="Background processing is unavailable. Please try again shortly.",
        )
    await repo.refresh_batch_status(candidate.batch_id)
    await repo.record_audit(
        action="candidate_rerun_requested",
        actor_id=current_user.id,
        batch_id=candidate.batch_id,
        candidate_id=candidate.id,
        metadata={"queued": True},
    )
    return {"candidateId": candidate_id, "queued": True, "jobId": queued}


@router.patch("/candidates/{candidate_id}/review")
async def review_candidate(
    candidate_id: int,
    reviewer_notes: str = Form(""),
    reviewer_decision: str = Form(""),
    decision_reason: str = Form(""),
    interview_outcome: str = Form(""),
    assigned_reviewer_id: int | None = Form(None),
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    repo = BulkATSCRUDRepository(session)
    existing = await _get_authorized_candidate(
        repo, candidate_id=candidate_id, current_user=current_user
    )
    decision = reviewer_decision.strip() or None
    outcome = interview_outcome.strip() or None
    if decision not in {None, "shortlist", "hold", "not_shortlisted"}:
        raise HTTPException(status_code=400, detail="Invalid reviewer_decision")
    if outcome not in {
        None,
        "not_interviewed",
        "interviewed",
        "advanced",
        "rejected",
        "hired",
        "withdrew",
    }:
        raise HTTPException(status_code=400, detail="Invalid interview_outcome")
    if assigned_reviewer_id is not None:
        try:
            reviewer = await UserCRUDRepository(session).get_user_by_id(user_id=assigned_reviewer_id)
        except EntityDoesNotExist:
            reviewer = None
        if reviewer is None or reviewer.role not in {"recruiter", "placement_staff"}:
            raise HTTPException(status_code=400, detail="assigned_reviewer_id must belong to Bulk ATS staff")
    candidate = await repo.update_review(
        candidate_id,
        reviewer_notes=reviewer_notes[:10000],
        reviewer_decision=decision,
        decision_reason=decision_reason.strip()[:5000] or None,
        interview_outcome=outcome,
        reviewed_by=current_user.id,
        assigned_reviewer_id=assigned_reviewer_id,
    )
    if candidate is None:  # Defensive: it could only disappear between the two calls.
        raise HTTPException(status_code=404, detail="Candidate not found")
    await repo.record_audit(
        action="candidate_review_updated",
        actor_id=current_user.id,
        batch_id=existing.batch_id,
        candidate_id=candidate.id,
        metadata={"decision": decision, "interviewOutcome": outcome, "assignedReviewerId": assigned_reviewer_id},
    )
    return _candidate_detail(candidate)


@router.get("/batches/{batch_id}/audit-events")
async def list_audit_events(
    batch_id: int,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    repo = BulkATSCRUDRepository(session)
    await _get_authorized_batch(repo, batch_id=batch_id, current_user=current_user)
    events = await repo.list_audit_events(batch_id=batch_id)
    return [
        {
            "id": event.id,
            "action": event.action,
            "candidateId": event.candidate_id,
            "actorId": event.actor_id,
            "metadata": event.event_metadata or {},
            "createdAt": event.created_at,
        }
        for event in events
    ]


@router.get("/reviewers")
async def list_reviewers(
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    """Return assignable placement/recruiting staff for candidate queues."""
    del current_user
    staff = await UserCRUDRepository(session).list_bulk_ats_staff()
    return [
        {"id": user.id, "email": user.email, "name": user.name, "role": user.role}
        for user in staff
    ]


@router.get("/batches/{batch_id}/calibration")
async def get_batch_calibration(
    batch_id: int,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    """Return auditable outcome/score aggregates for recruiter calibration."""
    repo = BulkATSCRUDRepository(session)
    await _get_authorized_batch(repo, batch_id=batch_id, current_user=current_user)
    candidates = await repo.list_candidates(batch_id=batch_id, limit=1000, sort="score")
    scored = [c for c in candidates if c.final_score is not None]
    outcomes: dict[str, int] = {}
    decisions: dict[str, int] = {}
    for candidate in candidates:
        outcome = candidate.interview_outcome or "not_recorded"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        decision = candidate.reviewer_decision or "not_decided"
        decisions[decision] = decisions.get(decision, 0) + 1
    average = (sum(c.final_score or 0 for c in scored) / len(scored)) if scored else None
    hired = [c.final_score for c in candidates if c.interview_outcome == "hired" and c.final_score is not None]
    rejected = [c.final_score for c in candidates if c.interview_outcome == "rejected" and c.final_score is not None]
    return {
        "batchId": batch_id,
        "candidateCount": len(candidates),
        "scoredCount": len(scored),
        "averageScore": average,
        "outcomes": outcomes,
        "decisions": decisions,
        "hiredAverageScore": (sum(hired) / len(hired)) if hired else None,
        "rejectedAverageScore": (sum(rejected) / len(rejected)) if rejected else None,
        "calibrationNote": "Use these aggregates to inspect recruiter overrides and outcome patterns; they do not retrain scoring automatically.",
    }


@router.post("/candidates/{candidate_id}/interview-kit")
async def generate_interview_kit(
    candidate_id: int,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    """Create a deterministic, evidence-aware interview guide for human review."""
    repo = BulkATSCRUDRepository(session)
    candidate = await _get_authorized_candidate(repo, candidate_id=candidate_id, current_user=current_user)
    report = candidate.deterministic_report or {}
    scorecard = report.get("bulkAtsScorecard") if isinstance(report, dict) else {}
    scorecard = scorecard if isinstance(scorecard, dict) else {}
    category_scores = scorecard.get("categoryScores") or {}
    metrics = {
        "skills": "Ask the candidate to demonstrate the most important required capability with a concrete example.",
        "experience": "Ask how the candidate's most relevant responsibility maps to this role and what outcome they owned.",
        "projects": "Ask the candidate to walk through the strongest project, their contribution, trade-offs, and measurable result.",
        "education": "Confirm how the candidate's education or training supports the role's responsibilities.",
        "links": "Review one submitted work link and ask the candidate to explain authorship, decisions, and current status.",
    }
    questions = []
    for category, prompt in metrics.items():
        value = category_scores.get(category)
        if value is None or value < 70:
            questions.append({"category": category, "question": prompt, "reason": "Score or evidence gap needs human validation."})
    if not questions:
        questions.append({"category": "verification", "question": "Which accomplishment best demonstrates readiness for this role, and what evidence can you share?", "reason": "Confirm the strongest evidence before advancing."})
    await repo.record_audit(
        action="interview_kit_generated",
        actor_id=current_user.id,
        batch_id=candidate.batch_id,
        candidate_id=candidate.id,
        metadata={"questionCount": len(questions)},
    )
    return {"candidateId": candidate.id, "questions": questions, "generatedAt": datetime.datetime.now(datetime.timezone.utc)}


@router.get("/google-drive/status")
async def get_google_drive_status(
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    repo = BulkATSCRUDRepository(session)
    credential = await repo.get_drive_credential(user_id=current_user.id)
    configured = drive_oauth_is_configured()
    return {
        "configured": configured,
        "configurationErrors": [] if configured else drive_oauth_configuration_errors(),
        "connected": credential is not None,
        "googleEmail": credential.google_email if credential else None,
        "scopes": credential.scopes if credential else [],
        "lastUsedAt": credential.last_used_at if credential else None,
    }


@router.post("/google-drive/connect")
async def connect_google_drive(
    response: fastapi.Response,
    current_user: User = Depends(get_current_bulk_ats_user),
):
    if not drive_oauth_is_configured():
        raise HTTPException(
            status_code=503,
            detail="Google Drive OAuth is not configured on this deployment",
        )
    authorization_url, state = build_authorization_request(user_id=current_user.id)
    response.set_cookie(
        "bulk_ats_drive_oauth_state",
        state,
        max_age=settings.GOOGLE_DRIVE_OAUTH_STATE_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.GOOGLE_DRIVE_OAUTH_REDIRECT_URI.startswith("https://"),
        samesite="lax",
        path=f"{settings.API_PREFIX}/bulk-ats/google-drive",
    )
    return {"authorizationUrl": authorization_url}


@router.get("/google-drive/callback", include_in_schema=False)
async def google_drive_callback(
    request: fastapi.Request,
    state: str = "",
    code: str = "",
    error: str = "",
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    if error:
        return _drive_oauth_redirect(
            status="error", detail="Google Drive access was not approved"
        )
    if not state or not code:
        return _drive_oauth_redirect(
            status="error", detail="Google Drive callback was incomplete"
        )
    state_cookie = request.cookies.get("bulk_ats_drive_oauth_state", "")
    if not state_cookie or not secrets.compare_digest(state_cookie, state):
        return _drive_oauth_redirect(
            status="error", detail="Google Drive browser session did not match"
        )
    try:
        user_id = decode_state(state)
        user = await UserCRUDRepository(async_session=session).get_user_by_id(
            user_id=user_id
        )
        if not is_bulk_ats_user(user):
            return _drive_oauth_redirect(
                status="error", detail="Bulk ATS access is no longer available"
            )
        grant = await exchange_authorization_code(code=code)
        repo = BulkATSCRUDRepository(session)
        await repo.upsert_drive_credential(
            user_id=user.id,
            encrypted_refresh_token=encrypt_refresh_token(grant.refresh_token),
            google_email=grant.email,
            scopes=grant.scopes,
            key_id=settings.GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY_ID,
        )
        await repo.record_audit(
            action="google_drive_connected",
            actor_id=user.id,
            metadata={"scopes": grant.scopes},
        )
        return _drive_oauth_redirect(status="connected")
    except Exception as exc:
        logger.exception("Google Drive OAuth callback failed")
        return _drive_oauth_redirect(
            status="error", detail="Google Drive connection could not be completed"
        )


@router.delete("/google-drive/connection", status_code=204)
async def disconnect_google_drive(
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    repo = BulkATSCRUDRepository(session)
    credential = await repo.get_drive_credential(user_id=current_user.id)
    if credential is None:
        return None
    try:
        await revoke_refresh_token(
            encrypted_refresh_token=credential.encrypted_refresh_token,
            key_id=credential.key_id,
        )
    except Exception:
        # Revocation is best effort: removing the local refresh token still
        # prevents this application from accessing Drive again.
        logger.warning(
            "Google Drive token revocation failed for user=%s", current_user.id
        )
    await repo.delete_drive_credential(user_id=current_user.id)
    await repo.record_audit(
        action="google_drive_disconnected", actor_id=current_user.id
    )
    return None


@router.get("/me")
async def get_bulk_ats_self(current_user: User = Depends(get_current_bulk_ats_user)):
    """Lets the frontend decide what to show (e.g. the staff-access admin tab)
    without duplicating the RBAC logic in get_current_bulk_ats_user client-side.
    Reaching this route at all already proves bulk-ats access; isAdmin further
    distinguishes who may grant/revoke it for others."""
    import shutil

    return {
        "email": current_user.email,
        "isAdmin": is_admin_user(current_user),
        "jevConfigured": bool(settings.JEV_API_KEY),
        "deepseekConfigured": bool(settings.DEEPSEEK_API_KEY),
        "ocrEnabled": settings.BULK_ATS_OCR_ENABLED,
        "ocrAvailable": settings.BULK_ATS_OCR_ENABLED
        and shutil.which("tesseract") is not None,
        "malwareScannerConfigured": bool(settings.BULK_ATS_CLAMAV_HOST.strip()),
        "googleDriveOAuthConfigured": drive_oauth_is_configured(),
    }


class SetBulkATSRoleBody(pydantic.BaseModel):
    email: pydantic.EmailStr
    role: Literal["recruiter", "placement_staff"] | None = None


@router.get("/admin/staff")
async def list_bulk_ats_staff(
    current_user: User = Depends(get_current_admin_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    """Admin-only: who currently has Bulk ATS access. Gated by
    is_admin_user (the existing is_admin/ADMIN_EMAILS grant,
    reachable today via SamvaadAdminsTab) rather than
    get_current_bulk_ats_user, so granting access can't be bootstrapped by
    someone who only has access themselves."""
    if not is_admin_user(current_user):
        raise HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )
    user_repo = UserCRUDRepository(async_session=session)
    staff = await user_repo.list_bulk_ats_staff()
    return [{"email": u.email, "name": u.name, "role": u.role} for u in staff]


@router.post("/admin/staff")
async def set_bulk_ats_staff_role(
    payload: SetBulkATSRoleBody,
    current_user: User = Depends(get_current_admin_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    if not is_admin_user(current_user):
        raise HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )
    from src.utilities.exceptions.database import EntityDoesNotExist

    user_repo = UserCRUDRepository(async_session=session)
    try:
        user = await user_repo.get_user_by_email(email=payload.email)
    except EntityDoesNotExist:
        raise HTTPException(
            status_code=404,
            detail="No Samvaad Saathi account exists for this email yet",
        )
    user = await user_repo.set_bulk_ats_role(email=user.email, role=payload.role)
    return {"email": user.email, "role": user.role}
