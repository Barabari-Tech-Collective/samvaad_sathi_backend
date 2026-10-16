"""arq task functions - run in the separate `arq` worker process, not inline
in a request. Add new background-safe operations here as this grows (e.g.
non-real-time report regeneration); real-time transcription (Whisper) stays
synchronous since the interview flow can't proceed without it.
"""

import asyncio
import datetime
import logging
from dataclasses import dataclass

from arq.worker import Retry

from src.services.barabari_integration import submit_resume_score_to_barabari

logger = logging.getLogger(__name__)

_RESUME_FETCH_MAX_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class ResumeFetchResult:
    content: bytes | None
    filename: str | None = None
    content_type: str | None = None
    error: str | None = None


def _google_drive_file_id(url: str) -> str | None:
    """Extract a file ID from common Google Drive sharing URL forms."""
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    if (parsed.hostname or "").casefold() not in {
        "drive.google.com",
        "www.drive.google.com",
    }:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    file_id = (
        parts[2] if len(parts) >= 3 and parts[0] == "file" and parts[1] == "d" else None
    )
    if file_id is None:
        file_id = parse_qs(parsed.query).get("id", [None])[0]
    return file_id


def _google_drive_download_url(url: str) -> str:
    """Convert common Drive sharing links into the public download endpoint."""
    from urllib.parse import urlencode, urlparse, urlunparse

    file_id = _google_drive_file_id(url)
    if not file_id:
        return url
    parsed = urlparse(url)
    return urlunparse(
        (
            "https",
            "drive.google.com",
            "/uc",
            "",
            urlencode({"export": "download", "id": file_id}),
            "",
        )
    )


def _is_public_http_url(url: str) -> bool:
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, None)}
    except socket.gaierror:
        return False
    return bool(addresses) and all(
        ipaddress.ip_address(address).is_global for address in addresses
    )


def _detect_resume_type(content: bytes) -> tuple[str, str] | None:
    import io
    import zipfile

    if content.startswith(b"%PDF-"):
        return ".pdf", "application/pdf"
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" in names and "word/document.xml" in names:
                return (
                    ".docx",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
    except (zipfile.BadZipFile, OSError):
        pass
    return None


def _safe_download_filename(
    *, content_disposition: str, url: str, extension: str
) -> str:
    import os
    import re
    from urllib.parse import unquote, urlparse

    match = re.search(
        r"filename\*?=(?:UTF-8''|\")?([^\";]+)",
        content_disposition,
        flags=re.IGNORECASE,
    )
    candidate = (
        unquote(match.group(1)).strip()
        if match
        else os.path.basename(urlparse(url).path)
    )
    candidate = os.path.basename(candidate)[:240] or f"resume{extension}"
    if not candidate.casefold().endswith(extension):
        candidate = f"{candidate.rsplit('.', 1)[0]}{extension}"
    return candidate


async def _fetch_resume_from_url(
    url: str, *, google_access_token: str | None = None
) -> ResumeFetchResult:
    """Downloads a candidate resume linked from an Excel/CSV import.

    Only http(s) with a public host - rejects loopback/link-local/private
    ranges so an Excel row can't be used to probe internal services (SSRF).
    Streams with a byte cap rather than trusting Content-Length, which a
    malicious or misconfigured server can omit or lie about.
    """
    from urllib.parse import urljoin, urlparse

    import httpx

    drive_file_id = _google_drive_file_id(url)
    current_url = (
        f"https://www.googleapis.com/drive/v3/files/{drive_file_id}?alt=media&supportsAllDrives=true"
        if drive_file_id and google_access_token
        else _google_drive_download_url(url)
    )
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            for _redirect_count in range(6):
                if not _is_public_http_url(current_url):
                    return ResumeFetchResult(
                        None,
                        error="Resume link is invalid or does not resolve to a public host",
                    )
                request_headers = (
                    {"Authorization": f"Bearer {google_access_token}"}
                    if google_access_token
                    and (urlparse(current_url).hostname or "").casefold()
                    == "www.googleapis.com"
                    else {}
                )
                stream_kwargs = {"headers": request_headers} if request_headers else {}
                async with client.stream(
                    "GET", current_url, **stream_kwargs
                ) as response:
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("location")
                        if not location:
                            return ResumeFetchResult(
                                None,
                                error="Resume link redirected without a destination",
                            )
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code >= 400:
                        return ResumeFetchResult(
                            None,
                            error=f"Resume link returned HTTP {response.status_code}",
                        )

                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > _RESUME_FETCH_MAX_BYTES:
                            return ResumeFetchResult(
                                None, error="Resume file exceeds the 10 MB limit"
                            )

                    content = bytes(chunks)
                    detected = _detect_resume_type(content)
                    if detected is None:
                        return ResumeFetchResult(
                            None,
                            error="Resume link did not return a PDF or DOCX file; check Drive sharing permissions",
                        )
                    extension, content_type = detected
                    filename = _safe_download_filename(
                        content_disposition=response.headers.get(
                            "content-disposition", ""
                        ),
                        url=current_url,
                        extension=extension,
                    )
                    return ResumeFetchResult(
                        content, filename=filename, content_type=content_type
                    )
            return ResumeFetchResult(
                None, error="Resume link exceeded the redirect limit"
            )
    except Exception as exc:
        logger.warning("Bulk ATS: failed to fetch resume_url: %s", exc)
        return ResumeFetchResult(None, error="Resume link could not be downloaded")


async def generate_questions_task(
    ctx: dict,
    *,
    job_profile_id: int,
    levels: list[dict],
    knowledge_reference_context: str | None = None,
) -> dict:
    """Generate interview questions for a job profile and persist them to the DB.

    ``levels`` is a list of dicts with keys ``level`` (int) and ``count`` (int).
    Returns ``{"count": <number of questions saved>}`` on success.
    Raises on failure so arq marks the job as failed.
    """
    from src.repository.crud.job_profile import JobProfileCRUDRepository
    from src.repository.database import async_db
    from src.services.question_generation import generate_questions_for_level

    session = async_db.get_session()
    try:
        repo = JobProfileCRUDRepository(async_session=session)
        profile = await repo.get_by_id(job_profile_id=job_profile_id)
        if profile is None:
            raise ValueError(f"Job profile {job_profile_id} not found")

        track = profile.job_name
        context_text = profile.job_description
        skills_list = profile.skills or []

        level_tasks = [
            generate_questions_for_level(
                level=l["level"],
                count=l["count"],
                track=track,
                context_text=context_text,
                skills_list=skills_list,
                experience_level=profile.experience_level,
                category=profile.category,
                knowledge_reference_context=knowledge_reference_context,
            )
            for l in levels
        ]
        results = await asyncio.gather(*level_tasks)

        generated_questions_data = []
        for level_results in results:
            for level, difficulty, item in level_results:
                generated_questions_data.append(
                    {
                        "job_profile_id": profile.id,
                        "question_text": item["text"],
                        "level": level,
                        "difficulty": difficulty,
                        "question_type": item.get("category", "theoretical"),
                        "is_ai_generated": True,
                        "keywords": item.get("keywords") or [],
                        "concepts_covered": item.get("concepts_covered") or [],
                        "expected_answer": item.get("expected_answer"),
                        "example_output": item.get("example_output"),
                    }
                )

        db_questions = await repo.create_job_profile_questions(generated_questions_data)
        await session.commit()
        logger.info(
            "generate_questions_task: saved %d questions for job profile %d",
            len(db_questions),
            job_profile_id,
        )
        return {"count": len(db_questions)}
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def submit_resume_score_task(
    ctx: dict,
    *,
    student_id: str,
    resume_score: int,
    request_id: str,
    target_role: str | None = None,
) -> None:
    """Wraps submit_resume_score_to_barabari for arq. Converts a plain
    exception into arq.worker.Retry with an escalating delay (2s, 4s, ...) so
    a briefly-down Barabari endpoint isn't hammered again within the same
    second - arq retries immediately otherwise. WorkerSettings.max_tries
    caps the total attempts."""
    try:
        await submit_resume_score_to_barabari(
            student_id=student_id,
            resume_score=resume_score,
            request_id=request_id,
            target_role=target_role,
        )
    except Exception as exc:
        job_try = ctx.get("job_try", 1)
        raise Retry(defer=2 * job_try) from exc


async def process_bulk_ats_candidate_task(ctx: dict, *, candidate_id: int) -> dict:
    """Runs the Bulk ATS pipeline for one candidate and persists the result.

    Does not raise/Retry on a pipeline failure - run_candidate_pipeline
    already turns parse/scoring errors into a "failed" CandidateResult, and
    one bad resume in a batch of hundreds should land that one row as failed
    rather than consume arq retries or block the rest of the batch. This
    only raises (uncaught) for genuine infrastructure errors - DB down,
    candidate row vanished - which arq's default retry handles.
    """
    from src.repository.crud.bulk_ats import BulkATSCRUDRepository
    from src.repository.database import async_db
    from src.services.ai_resume.bulk.pipeline import run_candidate_pipeline
    from src.services.ai_resume.bulk.jev_client import get_jev_provider
    from src.services.ai_resume.bulk.malware_scan import scan_document
    from src.services.ai_resume.bulk.deepseek_feedback import enrich_candidate_brief
    from src.services.s3_service import fetch_bulk_ats_resume_from_s3

    session = async_db.get_session()
    try:
        repo = BulkATSCRUDRepository(session)
        candidate = await repo.get_candidate(candidate_id)
        if candidate is None:
            raise ValueError(f"Bulk ATS candidate {candidate_id} not found")

        batch = candidate.batch
        if batch is None:
            raise ValueError(f"Bulk ATS candidate {candidate_id} has no batch")

        await repo.update_candidate_status(candidate_id, "parsing")

        raw_bytes: bytes | None = None
        fetched: ResumeFetchResult | None = None
        if candidate.s3_key:
            raw_bytes = await fetch_bulk_ats_resume_from_s3(candidate.s3_key)
        elif candidate.resume_url:
            # Excel/link-import candidates arrive with only a resume_url - fetch
            # once here (not at upload time) so a bad/slow link fails this one
            # candidate's job instead of the whole import request, and persist
            # to S3 so a rerun doesn't have to re-fetch the external URL.
            google_access_token: str | None = None
            if _google_drive_file_id(candidate.resume_url) and batch.created_by:
                credential = await repo.get_drive_credential(user_id=batch.created_by)
                if credential is not None:
                    try:
                        from src.services.ai_resume.bulk.google_drive_oauth import (
                            refresh_access_token,
                        )

                        google_access_token = await refresh_access_token(
                            encrypted_refresh_token=credential.encrypted_refresh_token,
                            key_id=credential.key_id,
                        )
                        await repo.mark_drive_credential_used(user_id=batch.created_by)
                    except Exception:
                        logger.exception(
                            "Private Google Drive authorization failed for batch=%s",
                            batch.id,
                        )
                        await repo.update_candidate_status(
                            candidate_id,
                            "failed",
                            error_message="Google Drive authorization expired or was revoked. Reconnect Drive and rerun this candidate.",
                        )
                        await repo.refresh_batch_status(batch.id)
                        return {"status": "failed"}
            fetched = await _fetch_resume_from_url(
                candidate.resume_url,
                google_access_token=google_access_token,
            )
            raw_bytes = fetched.content
            if raw_bytes is not None:
                from src.services.s3_service import upload_bulk_ats_resume_to_s3

                filename = (
                    fetched.filename or candidate.original_filename or "resume.pdf"
                )
                content_type = fetched.content_type or "application/octet-stream"
                s3_key = upload_bulk_ats_resume_to_s3(
                    raw_bytes, batch.id, filename, content_type
                )
                if s3_key:
                    await repo.save_candidate_result(
                        candidate_id, s3_key=s3_key, original_filename=filename
                    )

        if raw_bytes is None:
            fetch_error = fetched.error if fetched else None
            await repo.update_candidate_status(
                candidate_id,
                "failed",
                error_message=fetch_error or "Resume file could not be retrieved",
            )
            await repo.refresh_batch_status(batch.id)
            return {"status": "failed"}

        malware = await scan_document(raw_bytes)
        if malware["status"] in {"infected", "error"}:
            await repo.update_candidate_status(
                candidate_id,
                "failed",
                error_message="Resume failed the document malware safety scan",
            )
            await repo.refresh_batch_status(batch.id)
            return {"status": "failed"}

        await repo.update_candidate_status(candidate_id, "evaluating")

        job_profile = None
        if batch.job_profile_id:
            from src.repository.crud.job_profile import JobProfileCRUDRepository

            job_profile = await JobProfileCRUDRepository(
                async_session=session
            ).get_by_id(job_profile_id=batch.job_profile_id)
        role_description = (
            job_profile.job_description
            if job_profile
            else (batch.role_description or "")
        )
        required_skills = (
            (job_profile.skills or []) if job_profile else (batch.required_skills or [])
        )
        preferred_skills = batch.preferred_skills or []

        try:
            result = await run_candidate_pipeline(
                raw_bytes=raw_bytes,
                filename=(fetched.filename if fetched else None)
                or candidate.original_filename
                or "resume.pdf",
                role_name=batch.role_name,
                role_description=role_description,
                evaluation_intent=batch.evaluation_intent or "",
                required_skills=required_skills,
                preferred_skills=preferred_skills,
                experience_mode=batch.experience_mode,
                weight_overrides=batch.weight_overrides,
                jev_provider=get_jev_provider(),
            )
        except Exception as exc:
            logger.exception(
                "Bulk ATS evaluation crashed for candidate=%s", candidate_id
            )
            await repo.update_candidate_status(
                candidate_id,
                "failed",
                error_message=f"Evaluation failed unexpectedly: {exc}",
            )
            await repo.refresh_batch_status(batch.id)
            return {"status": "failed"}

        parsed_header = (result.parsed_resume_json or {}).get("header", {})
        parsed_name = (
            parsed_header.get("fullName") if isinstance(parsed_header, dict) else None
        )
        parsed_email = (
            parsed_header.get("email") if isinstance(parsed_header, dict) else None
        )

        scorecard = (result.deterministic_report or {}).get("bulkAtsScorecard") or {}
        confidence = scorecard.get("confidence") or {}
        score_interval = confidence.get("scoreInterval") or {}
        if result.deterministic_report and scorecard:
            brief, brief_meta = await enrich_candidate_brief(
                role_name=batch.role_name,
                scorecard=scorecard,
                evidence=scorecard.get("evidence") or {},
                warnings=result.warnings,
                deterministic_brief=result.deterministic_report.get("candidateBrief")
                or {},
                existing_report=candidate.deterministic_report,
                evaluation_intent=batch.evaluation_intent or "",
            )
            result.deterministic_report["candidateBrief"] = brief
            result.deterministic_report["candidateBriefMeta"] = brief_meta

        await repo.save_candidate_result(
            candidate_id,
            candidate_name=candidate.candidate_name
            or (str(parsed_name).strip()[:256] if parsed_name else None),
            email_or_candidate_id=(
                candidate.email_or_candidate_id
                or (str(parsed_email).strip()[:256] if parsed_email else None)
            ),
            status=result.status,
            error_message=result.error_message,
            extracted_resume_text=result.extracted_resume_text,
            parsed_resume_json=result.parsed_resume_json,
            deterministic_report=result.deterministic_report,
            jev_report=result.jev_report,
            link_report=result.link_report,
            final_score=result.final_score,
            score_band=result.score_band,
            needs_review=result.needs_review,
            warnings=result.warnings,
            confidence_score=confidence.get("overall"),
            score_lower_bound=score_interval.get("lower"),
            score_upper_bound=score_interval.get("upper"),
        )
        await repo.record_audit(
            action="candidate_evaluated",
            actor_id=None,
            batch_id=batch.id,
            candidate_id=candidate_id,
            metadata={
                "status": result.status,
                "score": result.final_score,
                "confidence": confidence.get("overall"),
                "feedbackSource": (
                    (result.deterministic_report or {}).get("candidateBriefMeta") or {}
                ).get("source"),
            },
        )
        await repo.refresh_batch_status(batch.id)
        logger.info(
            "process_bulk_ats_candidate_task: candidate=%s status=%s score=%s",
            candidate_id,
            result.status,
            result.final_score,
        )
        return {"status": result.status, "final_score": result.final_score}
    finally:
        await session.close()


async def generate_bulk_ats_feedback_task(
    ctx: dict,
    *,
    candidate_id: int,
    audience: str,
    request_id: str,
    requested_by: int | None = None,
) -> dict:
    """Generate one explicitly requested DeepSeek feedback artifact.

    The request token makes duplicate clicks and concurrent recruiters safe:
    only the latest persisted request for a candidate/audience may spend a
    provider call or write a result.
    """

    from src.repository.crud.bulk_ats import BulkATSCRUDRepository
    from src.repository.database import async_db
    from src.services.ai_resume.bulk.deepseek_feedback import (
        ShareableFeedbackError,
        generate_shareable_feedback,
    )

    if audience not in {"student", "team"}:
        return {"status": "failed", "error": "invalid audience"}

    session = async_db.get_session()
    try:
        repo = BulkATSCRUDRepository(session)
        candidate = await repo.get_candidate(candidate_id)
        if candidate is None or candidate.batch is None:
            return {"status": "failed", "error": "candidate not found"}
        batch = candidate.batch

        report = candidate.deterministic_report or {}
        shareable = report.get("shareableFeedback") or {}
        current = shareable.get(audience) or {}
        if current.get("requestId") != request_id:
            return {"status": "superseded"}

        generating_state = {
            **current,
            "status": "generating",
            "startedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        candidate, updated = await repo.update_feedback_state(
            candidate_id,
            audience=audience,
            state=generating_state,
            expected_request_id=request_id,
        )
        if not updated or candidate is None:
            return {"status": "superseded"}

        latest_report = candidate.deterministic_report or {}
        scorecard = latest_report.get("bulkAtsScorecard") or {}
        scorecard_for_feedback = {
            **scorecard,
            "overallScore": candidate.final_score,
        }
        try:
            content, meta = await generate_shareable_feedback(
                audience=audience,  # type: ignore[arg-type]
                role_name=batch.role_name,
                evaluation_intent=batch.evaluation_intent or "",
                scorecard=scorecard_for_feedback,
                evidence=scorecard.get("evidence") or {},
                warnings=candidate.warnings or [],
            )
        except ShareableFeedbackError as exc:
            failed_state = {
                **generating_state,
                "status": "failed",
                "error": str(exc),
                "completedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            await repo.update_feedback_state(
                candidate_id,
                audience=audience,
                state=failed_state,
                expected_request_id=request_id,
            )
            await repo.record_audit(
                action="candidate_feedback_failed",
                actor_id=requested_by,
                batch_id=candidate.batch_id,
                candidate_id=candidate_id,
                metadata={"audience": audience, "requestId": request_id},
            )
            return {"status": "failed", "error": str(exc)}

        completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        ready_state = {
            **generating_state,
            "status": "ready",
            "completedAt": completed_at,
            "content": content,
            "meta": {**meta, "generatedAt": completed_at},
        }
        _, updated = await repo.update_feedback_state(
            candidate_id,
            audience=audience,
            state=ready_state,
            expected_request_id=request_id,
        )
        if not updated:
            return {"status": "superseded"}
        await repo.record_audit(
            action="candidate_feedback_generated",
            actor_id=requested_by,
            batch_id=candidate.batch_id,
            candidate_id=candidate_id,
            metadata={
                "audience": audience,
                "requestId": request_id,
                "source": meta.get("source"),
                "model": meta.get("model"),
            },
        )
        return {"status": "ready", "audience": audience}
    finally:
        await session.close()


async def purge_expired_bulk_ats_batches_task(ctx: dict) -> dict:
    """Daily retention enforcement; never removes DB records if S3 cleanup fails."""
    import datetime

    from src.repository.crud.bulk_ats import BulkATSCRUDRepository
    from src.repository.database import async_db
    from src.services.s3_service import delete_bulk_ats_resumes_from_s3

    session = async_db.get_session()
    purged = 0
    skipped = 0
    try:
        repo = BulkATSCRUDRepository(session)
        batches = await repo.list_expired_batches(
            now=datetime.datetime.now(datetime.timezone.utc), limit=50
        )
        for batch in batches:
            candidates = await repo.list_candidates(
                batch_id=batch.id, limit=1000, sort="created"
            )
            deletion = await delete_bulk_ats_resumes_from_s3(
                [candidate.s3_key for candidate in candidates if candidate.s3_key]
            )
            if deletion["errors"]:
                skipped += 1
                logger.error(
                    "Retention purge retained batch=%s because S3 deletion failed",
                    batch.id,
                )
                continue
            await repo.record_audit(
                action="batch_retention_expired",
                actor_id=None,
                batch_id=batch.id,
                metadata={"storedResumesDeleted": deletion["deleted"]},
            )
            await repo.delete_batch(batch)
            purged += 1
        return {"purged": purged, "skipped": skipped}
    finally:
        await session.close()
