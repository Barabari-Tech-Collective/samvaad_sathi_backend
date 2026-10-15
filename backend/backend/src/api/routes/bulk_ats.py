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
from src.repository.crud.bulk_ats import BulkATSCRUDRepository
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

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(prefix="/bulk-ats", tags=["bulk-ats"])

_ALLOWED_EXCEL_CONTENT_TYPES = {
    "text/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


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
    }


def _candidate_detail(c: BulkATSCandidate) -> dict:
    return {
        **_candidate_summary(c),
        "originalFilename": c.original_filename,
        "resumeUrl": c.resume_url,
        "errorMessage": c.error_message,
        "deterministicReport": c.deterministic_report,
        "jevReport": c.jev_report,
        "linkReport": c.link_report,
        "reviewerNotes": c.reviewer_notes,
        "decisionReason": c.decision_reason,
        "outcomeRecordedAt": c.outcome_recorded_at,
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
    if retention_days < 1 or retention_days > settings.BULK_ATS_MAX_RETENTION_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"retention_days must be between 1 and {settings.BULK_ATS_MAX_RETENTION_DAYS}",
        )
    jd_analysis = analyze_job_description(
        role_description,
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
        role_description=role_description.strip() or None,
        evaluation_intent=normalized_intent or None,
        required_skills=parsed_required_skills or None,
        preferred_skills=parsed_preferred_skills or None,
        experience_mode=experience_mode,
        job_profile_id=job_profile_id,
        weight_overrides=None,
        jd_analysis=jd_analysis,
        retention_until=retention_until,
        created_by=current_user.id,
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
        "retentionUntil": batch.retention_until,
    }


@router.get("/batches")
async def list_batches(
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    limit: int = 50,
    offset: int = 0,
):
    repo = BulkATSCRUDRepository(session)
    batches = await repo.list_batches(
        created_by=None if is_admin_user(current_user) else current_user.id,
        limit=min(limit, 200),
        offset=offset,
    )
    return [
        {
            "id": b.id,
            "name": b.name,
            "roleName": b.role_name,
            "status": b.status,
            "createdAt": b.created_at,
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
        "retentionUntil": batch.retention_until,
        "status": batch.status,
        "createdAt": batch.created_at,
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
    file: UploadFile = File(...),
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
    if file.content_type not in _ALLOWED_EXCEL_CONTENT_TYPES:
        raise HTTPException(
            status_code=400, detail="Only CSV or Excel (.xlsx) files are supported"
        )

    repo = BulkATSCRUDRepository(session)
    batch = await _get_authorized_batch(
        repo, batch_id=batch_id, current_user=current_user
    )

    rows = await _read_candidate_rows(file)
    if len(rows) > settings.BULK_ATS_MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"At most {settings.BULK_ATS_MAX_FILES_PER_BATCH} rows per import",
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
    )
    total = await repo.count_candidates(batch_id=batch_id, status=status)
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
    return _candidate_detail(candidate)


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
    else:
        await repo.refresh_batch_status(candidate.batch_id)
    await repo.record_audit(
        action="candidate_rerun_requested",
        actor_id=current_user.id,
        batch_id=candidate.batch_id,
        candidate_id=candidate.id,
        metadata={"queued": bool(queued)},
    )
    return {"candidateId": candidate_id, "queued": bool(queued)}


@router.patch("/candidates/{candidate_id}/review")
async def review_candidate(
    candidate_id: int,
    reviewer_notes: str = Form(""),
    reviewer_decision: str = Form(""),
    decision_reason: str = Form(""),
    interview_outcome: str = Form(""),
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
    candidate = await repo.update_review(
        candidate_id,
        reviewer_notes=reviewer_notes[:10000],
        reviewer_decision=decision,
        decision_reason=decision_reason.strip()[:5000] or None,
        interview_outcome=outcome,
        reviewed_by=current_user.id,
    )
    if candidate is None:  # Defensive: it could only disappear between the two calls.
        raise HTTPException(status_code=404, detail="Candidate not found")
    await repo.record_audit(
        action="candidate_review_updated",
        actor_id=current_user.id,
        batch_id=existing.batch_id,
        candidate_id=candidate.id,
        metadata={"decision": decision, "interviewOutcome": outcome},
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
    get_current_admin_user (the existing is_admin/ADMIN_EMAILS grant,
    reachable today via SamvaadAdminsTab) rather than
    get_current_bulk_ats_user, so granting access can't be bootstrapped by
    someone who only has access themselves."""
    user_repo = UserCRUDRepository(async_session=session)
    staff = await user_repo.list_bulk_ats_staff()
    return [{"email": u.email, "name": u.name, "role": u.role} for u in staff]


@router.post("/admin/staff")
async def set_bulk_ats_staff_role(
    payload: SetBulkATSRoleBody,
    current_user: User = Depends(get_current_admin_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
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
