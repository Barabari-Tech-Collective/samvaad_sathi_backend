"""Bulk ATS: screen many resumes against one role profile at once.

Backend-level RBAC (see get_current_bulk_ats_user) keeps this unavailable to
students regardless of UI - every route here depends on it, not on
get_current_user alone. This is screening *assistance*: nothing here
rejects or auto-advances a candidate: it only scores and flags for a human
reviewer (needs_review).
"""

import hashlib
import logging
from typing import Optional

import fastapi
from fastapi import UploadFile, File, Form, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession

from src.api.dependencies.admin import get_current_bulk_ats_user
from src.api.dependencies.rate_limit import hybrid_rate_limiter
from src.api.dependencies.session import get_async_session
from src.config.manager import settings
from src.models.db.bulk_ats import BulkATSCandidate
from src.models.db.user import User
from src.repository.crud.bulk_ats import BulkATSCRUDRepository
from src.services.ai_resume.parser_service import ALLOWED_FILE_TYPES
from src.services.s3_service import upload_bulk_ats_resume_to_s3
from src.worker.queue import enqueue_job

logger = logging.getLogger(__name__)

router = fastapi.APIRouter(prefix="/bulk-ats", tags=["bulk-ats"])

_ALLOWED_EXCEL_CONTENT_TYPES = {
    "text/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


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
        "createdAt": c.created_at,
        "updatedAt": c.updated_at,
    }


@router.post("/batches", status_code=201)
async def create_batch(
    name: str = Form(...),
    role_name: str = Form(...),
    experience_mode: str = Form("experienced"),
    job_profile_id: Optional[int] = Form(None),
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    if experience_mode not in ("fresher", "experienced"):
        raise HTTPException(status_code=400, detail="experience_mode must be 'fresher' or 'experienced'")
    if not name.strip() or not role_name.strip():
        raise HTTPException(status_code=400, detail="name and role_name are required")

    repo = BulkATSCRUDRepository(session)
    batch = await repo.create_batch(
        name=name.strip(),
        role_name=role_name.strip(),
        experience_mode=experience_mode,
        job_profile_id=job_profile_id,
        weight_overrides=None,
        created_by=current_user.id,
    )
    return {"id": batch.id, "name": batch.name, "roleName": batch.role_name, "status": batch.status}


@router.get("/batches")
async def list_batches(
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    limit: int = 50,
    offset: int = 0,
):
    repo = BulkATSCRUDRepository(session)
    batches = await repo.list_batches(limit=min(limit, 200), offset=offset)
    return [
        {"id": b.id, "name": b.name, "roleName": b.role_name, "status": b.status, "createdAt": b.created_at}
        for b in batches
    ]


@router.get("/batches/{batch_id}")
async def get_batch(
    batch_id: int,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    repo = BulkATSCRUDRepository(session)
    batch = await repo.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    total = await repo.count_candidates(batch_id=batch_id)
    completed = await repo.count_candidates(batch_id=batch_id, status="completed")
    needs_review = await repo.count_candidates(batch_id=batch_id, status="needs_review")
    failed = await repo.count_candidates(batch_id=batch_id, status="failed")

    return {
        "id": batch.id,
        "name": batch.name,
        "roleName": batch.role_name,
        "experienceMode": batch.experience_mode,
        "status": batch.status,
        "createdAt": batch.created_at,
        "counts": {"total": total, "completed": completed, "needsReview": needs_review, "failed": failed, "pending": total - completed - needs_review - failed},
    }


@router.delete("/batches/{batch_id}", status_code=204)
async def delete_batch(
    batch_id: int,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    repo = BulkATSCRUDRepository(session)
    batch = await repo.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
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
    batch = await repo.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

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
                skipped.append({"filename": upload.filename, "reason": "unsupported file type"})
                continue

            raw_bytes = await upload.read()
            if len(raw_bytes) > settings.BULK_ATS_MAX_FILE_SIZE_MB * 1024 * 1024:
                skipped.append({"filename": upload.filename, "reason": "file too large"})
                continue

            sha256 = hashlib.sha256(raw_bytes).hexdigest()
            duplicate = await repo.find_duplicate(batch_id=batch_id, file_sha256=sha256, candidate_key=None)
            if duplicate is not None:
                skipped.append({"filename": upload.filename, "reason": "duplicate of an existing candidate in this batch"})
                continue

            s3_key = upload_bulk_ats_resume_to_s3(raw_bytes, batch_id, upload.filename or "resume.pdf", upload.content_type)
            if s3_key is None:
                skipped.append({"filename": upload.filename, "reason": "storage upload failed"})
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
            queued = await enqueue_job("process_bulk_ats_candidate_task", candidate_id=candidate.id)
            accepted.append({"candidateId": candidate.id, "filename": upload.filename, "queued": bool(queued)})
        except Exception as exc:
            logger.exception("Bulk ATS upload failed for file %s", upload.filename)
            skipped.append({"filename": upload.filename, "reason": str(exc)})

    await repo.update_batch_status(batch_id, "processing")
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
        raise HTTPException(status_code=400, detail="Only CSV or Excel (.xlsx) files are supported")

    repo = BulkATSCRUDRepository(session)
    batch = await repo.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    rows = await _read_candidate_rows(file)
    if len(rows) > settings.BULK_ATS_MAX_FILES_PER_BATCH:
        raise HTTPException(status_code=400, detail=f"At most {settings.BULK_ATS_MAX_FILES_PER_BATCH} rows per import")

    accepted: list[dict] = []
    skipped: list[dict] = []

    for row in rows:
        resume_url = (row.get("resume_url") or "").strip()
        email_or_id = (row.get("email_or_candidate_id") or "").strip()
        if not resume_url:
            skipped.append({"row": row, "reason": "missing resume_url"})
            continue

        candidate_key = f"{email_or_id.lower()}|{resume_url.lower()}" if email_or_id else resume_url.lower()
        duplicate = await repo.find_duplicate(batch_id=batch_id, file_sha256=None, candidate_key=candidate_key)
        if duplicate is not None:
            skipped.append({"row": row, "reason": "duplicate candidate in this batch"})
            continue

        candidate = BulkATSCandidate(
            batch_id=batch_id,
            candidate_name=(row.get("candidate_name") or "").strip() or None,
            email_or_candidate_id=email_or_id or None,
            candidate_key=candidate_key,
            resume_url=resume_url,
            original_filename=resume_url.rsplit("/", 1)[-1][:256],
            source=(row.get("source") or "excel_import").strip(),
            status="queued",
        )
        candidate = await repo.create_candidate(candidate)
        queued = await enqueue_job("process_bulk_ats_candidate_task", candidate_id=candidate.id)
        accepted.append({"candidateId": candidate.id, "queued": bool(queued)})

    await repo.update_batch_status(batch_id, "processing")
    return {"accepted": accepted, "skipped": skipped}


async def _read_candidate_rows(file: UploadFile) -> list[dict]:
    content = await file.read()
    filename = (file.filename or "").lower()

    if filename.endswith(".csv") or file.content_type == "text/csv":
        import csv
        import io

        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames:
            reader.fieldnames = [(name or "").strip().lower() for name in reader.fieldnames]
        return [dict(row) for row in reader]

    import io

    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    sheet = workbook.active
    rows_iter = sheet.iter_rows(values_only=True)
    try:
        header = [str(h).strip().lower() if h is not None else "" for h in next(rows_iter)]
    except StopIteration:
        return []

    results = []
    for row in rows_iter:
        if row is None or all(v is None for v in row):
            continue
        results.append({header[i]: (row[i] if i < len(row) else None) for i in range(len(header))})
    return results


@router.get("/batches/{batch_id}/candidates")
async def list_candidates(
    batch_id: int,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    repo = BulkATSCRUDRepository(session)
    batch = await repo.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    candidates = await repo.list_candidates(batch_id=batch_id, status=status, limit=min(limit, 200), offset=offset)
    total = await repo.count_candidates(batch_id=batch_id, status=status)
    return {"total": total, "candidates": [_candidate_summary(c) for c in candidates]}


@router.get("/candidates/{candidate_id}")
async def get_candidate(
    candidate_id: int,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    repo = BulkATSCRUDRepository(session)
    candidate = await repo.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return _candidate_detail(candidate)


@router.post("/candidates/{candidate_id}/rerun", status_code=202)
async def rerun_candidate(
    candidate_id: int,
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    repo = BulkATSCRUDRepository(session)
    candidate = await repo.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    await repo.update_candidate_status(candidate_id, "queued", error_message=None)
    queued = await enqueue_job("process_bulk_ats_candidate_task", candidate_id=candidate_id)
    return {"candidateId": candidate_id, "queued": bool(queued)}


@router.patch("/candidates/{candidate_id}/review")
async def review_candidate(
    candidate_id: int,
    reviewer_notes: str = Form(...),
    current_user: User = Depends(get_current_bulk_ats_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    repo = BulkATSCRUDRepository(session)
    candidate = await repo.update_review(candidate_id, reviewer_notes=reviewer_notes, reviewed_by=current_user.id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return _candidate_detail(candidate)
