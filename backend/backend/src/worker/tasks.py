"""arq task functions - run in the separate `arq` worker process, not inline
in a request. Add new background-safe operations here as this grows (e.g.
non-real-time report regeneration); real-time transcription (Whisper) stays
synchronous since the interview flow can't proceed without it.
"""

import asyncio
import logging

from arq.worker import Retry

from src.services.barabari_integration import submit_resume_score_to_barabari

logger = logging.getLogger(__name__)

_RESUME_FETCH_MAX_BYTES = 15 * 1024 * 1024  # 15MB; parser_service's own MAX_FILE_SIZE_MB is 10


async def _fetch_resume_from_url(url: str) -> bytes | None:
    """Downloads a candidate resume linked from an Excel/CSV import.

    Only http(s) with a public host - rejects loopback/link-local/private
    ranges so an Excel row can't be used to probe internal services (SSRF).
    Streams with a byte cap rather than trusting Content-Length, which a
    malicious or misconfigured server can omit or lie about.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    import httpx

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        logger.warning("Bulk ATS: rejecting non-http(s) resume_url")
        return None
    try:
        resolved_ips = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, None)}
        for ip in resolved_ips:
            if ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback or ipaddress.ip_address(ip).is_link_local:
                logger.warning("Bulk ATS: rejecting resume_url resolving to a private/loopback address")
                return None
    except socket.gaierror:
        return None

    try:
        # Redirects are not followed: a redirect target bypasses the IP check
        # above entirely (TOCTOU), so treating any redirect as a failure is
        # simpler and safer than re-validating each hop.
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > _RESUME_FETCH_MAX_BYTES:
                        logger.warning("Bulk ATS: resume_url exceeded size cap, aborting")
                        return None
                return bytes(chunks)
    except Exception as exc:
        logger.warning("Bulk ATS: failed to fetch resume_url: %s", exc)
        return None


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
                generated_questions_data.append({
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
                })

        db_questions = await repo.create_job_profile_questions(generated_questions_data)
        await session.commit()
        logger.info("generate_questions_task: saved %d questions for job profile %d", len(db_questions), job_profile_id)
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
        if candidate.s3_key:
            raw_bytes = await fetch_bulk_ats_resume_from_s3(candidate.s3_key)
        elif candidate.resume_url:
            # Excel/link-import candidates arrive with only a resume_url - fetch
            # once here (not at upload time) so a bad/slow link fails this one
            # candidate's job instead of the whole import request, and persist
            # to S3 so a rerun doesn't have to re-fetch the external URL.
            raw_bytes = await _fetch_resume_from_url(candidate.resume_url)
            if raw_bytes is not None:
                from src.services.s3_service import upload_bulk_ats_resume_to_s3

                content_type = "application/pdf" if candidate.resume_url.lower().endswith(".pdf") else "application/octet-stream"
                s3_key = upload_bulk_ats_resume_to_s3(raw_bytes, batch.id, candidate.original_filename or "resume.pdf", content_type)
                if s3_key:
                    await repo.save_candidate_result(candidate_id, s3_key=s3_key)

        if raw_bytes is None:
            await repo.update_candidate_status(candidate_id, "failed", error_message="Resume file could not be retrieved")
            return {"status": "failed"}

        await repo.update_candidate_status(candidate_id, "evaluating")

        job_profile = None
        if batch.job_profile_id:
            from src.repository.crud.job_profile import JobProfileCRUDRepository

            job_profile = await JobProfileCRUDRepository(async_session=session).get_by_id(job_profile_id=batch.job_profile_id)
        role_description = job_profile.job_description if job_profile else batch.role_name
        required_skills = (job_profile.skills or []) if job_profile else []

        result = await run_candidate_pipeline(
            raw_bytes=raw_bytes,
            filename=candidate.original_filename or "resume.pdf",
            role_name=batch.role_name,
            role_description=role_description,
            required_skills=required_skills,
            preferred_skills=[],
            experience_mode=batch.experience_mode,
            weight_overrides=batch.weight_overrides,
            jev_provider=get_jev_provider(),
        )

        await repo.save_candidate_result(
            candidate_id,
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
        )
        logger.info("process_bulk_ats_candidate_task: candidate=%s status=%s score=%s", candidate_id, result.status, result.final_score)
        return {"status": result.status, "final_score": result.final_score}
    finally:
        await session.close()
