import datetime
import hashlib
import logging
import time
from pathlib import Path

import fastapi
from src.services.interview_course_access import require_interview_course_access
from fastapi import File, Form, UploadFile

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.repository import get_repository
from src.models.db.question_attempt import (
    TERMINAL_TRANSCRIPTION_STATUSES,
    TranscriptionStatus,
)
from src.models.schemas.audio import (
    AudioTranscriptionResponse,
    FollowUpQuestion,
    TranscriptionStatusResponse,
)
from src.repository.crud.interview import InterviewCRUDRepository
from src.repository.crud.question import QuestionAttemptCRUDRepository
from src.services.audio_processor import (
    get_audio_duration_estimate,
    validate_audio_file,
)
from src.services.interview_lifecycle import interview_has_expired
from src.services.interview_session_lease import (
    session_lease_is_active,
    session_lease_matches,
)
from src.services.s3_service import upload_answer_audio_to_s3
from src.services.whisper import (
    extract_word_count,
    strip_word_level_data,
    validate_transcription_language,
)
from src.worker.queue import enqueue_job

router = fastapi.APIRouter(prefix="", tags=["audio"])
logger = logging.getLogger(__name__)


async def _require_active_interview(
    question_attempt,
    interview_repo,
    *,
    lease_token: str | None = None,
    user_id: int | None = None,
    lock: bool = False,
) -> object | None:
    if lock and user_id is not None and hasattr(interview_repo, "lock_by_id_and_user"):
        parent_interview = await interview_repo.lock_by_id_and_user(
            interview_id=question_attempt.interview_id, user_id=user_id
        )
    else:
        parent_interview = await interview_repo.get_by_id(
            interview_id=question_attempt.interview_id
        )
    if parent_interview is not None and (
        parent_interview.status == "expired"
        or (
            parent_interview.status in ("active", "paused")
            and interview_has_expired(parent_interview)
        )
    ):
        await interview_repo.mark_expired(interview_id=parent_interview.id)
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_410_GONE,
            detail="This Practice interview has expired. Start a new interview.",
        )
    if parent_interview is not None and parent_interview.status != "active":
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT,
            detail="This interview is paused or has ended and cannot accept answers.",
        )
    if parent_interview is not None:
        await require_interview_course_access(getattr(interview_repo, "async_session", None), parent_interview)
        from src.services.fixed_interview_lifecycle import require_answer_window
        require_answer_window(parent_interview, attempt=question_attempt)
    if (
        parent_interview is not None
        and getattr(parent_interview, "interview_mode", None) == "practice"
        and getattr(parent_interview, "session_lease_required", False)
    ):
        if not session_lease_matches(parent_interview, lease_token):
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_409_CONFLICT,
                detail={
                    "code": "SESSION_LEASE_REPLACED",
                    "message": "This interview is active on another device or tab.",
                },
            )
        if not session_lease_is_active(parent_interview):
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_409_CONFLICT,
                detail={
                    "code": "SESSION_LEASE_EXPIRED",
                    "message": "Reconnect before saving this answer.",
                },
            )
    return parent_interview


@router.post(
    path="/transcribe-whisper",
    name="audio:transcribe-whisper",
    response_model=AudioTranscriptionResponse,
    status_code=fastapi.status.HTTP_202_ACCEPTED,
    summary="Save an audio answer and queue speech processing",
    description=(
        "Accepts audio files (.mp3, .wav, .m4a, .flac) up to 25MB, stores the answer durably, "
        "and queues transcription and adaptive follow-up work. Requires a question attempt that "
        "belongs to the authenticated user."
    ),
)
async def transcribe_audio_answer(
    question_attempt_id: str = Form(
        ..., description="ID of the question attempt this audio answer belongs to"
    ),
    language: str = Form(
        default="en",
        description="Language code for transcription (e.g., 'en', 'es', 'fr')",
    ),
    file: UploadFile = File(
        ...,
        description="Audio file to transcribe. Supported: .mp3, .wav, .m4a, .flac (max 25MB)",
    ),
    session_lease_token: str | None = Form(default=None, min_length=20, max_length=256),
    current_user=fastapi.Depends(get_current_user),
    question_repo: QuestionAttemptCRUDRepository = fastapi.Depends(
        get_repository(repo_type=QuestionAttemptCRUDRepository)
    ),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
) -> AudioTranscriptionResponse:
    """
    Main endpoint for durable audio-answer workflow:
    1. Validate user owns the question attempt
    2. Validate and process audio file
    3. Store the audio and answer version
    4. Queue transcription and follow-up generation
    5. Acknowledge the saved answer without waiting for an AI provider
    """
    # Step 1: Verify question attempt exists and belongs to current user
    try:
        request_start = time.perf_counter()
        question_attempt_id_int = int(question_attempt_id)
    except ValueError:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid question_attempt_id format. Must be a valid integer.",
        )
    logger.info(
        "Transcription request started | User=%s | QuestionAttempt=%s | File=%s | Language=%s",
        current_user.id,
        question_attempt_id,
        file.filename,
        language,
    )
    question_attempt = await question_repo.get_by_id_and_user(
        question_attempt_id=question_attempt_id_int, user_id=current_user.id
    )

    logger.info(
        "Question attempt verified | User=%s | QuestionAttempt=%s",
        current_user.id,
        question_attempt_id_int,
    )

    if not question_attempt:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Question attempt not found or access denied",
        )

    # A terminated or completed interview must not accept a late answer for a
    # question attempt that was already created before it ended - otherwise a
    # proctoring termination only locks the student out of *starting* new
    # questions, not from finishing the one they were on when they left.
    parent_interview = await _require_active_interview(
        question_attempt,
        interview_repo,
        lease_token=session_lease_token,
        user_id=current_user.id,
    )

    # Step 2: Validate audio file
    try:
        validation_start = time.perf_counter()
        audio_bytes, file_metadata = await validate_audio_file(file)
        logger.info(
            "Audio validated | QuestionAttempt=%s | Size=%d bytes | Duration=%.2f sec",
            question_attempt_id_int,
            file_metadata["size"],
            time.perf_counter() - validation_start,
        )
    except fastapi.HTTPException:
        raise  # Re-raise validation errors as-is
    except Exception as e:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Audio file validation failed: {str(e)}",
        )

    # Step 3: Estimate audio duration
    duration_seconds = get_audio_duration_estimate(
        audio_bytes, file_metadata["content_type"]
    )

    # Step 4: Validate and normalize language
    validated_language = validate_transcription_language(language)
    # Persist the recording before any external AI call. A Whisper outage must
    # never turn a valid answer into data loss or block the Next button.
    checksum = hashlib.sha256(audio_bytes).hexdigest()
    same_answer = (
        question_attempt.answer_checksum == checksum
        and question_attempt.audio_url
        and question_attempt.answer_kind == "audio"
    )
    from src.services.interview_question_plan import planned_count
    fixed_plan = parent_interview is not None and planned_count(parent_interview) is not None
    immutable = fixed_plan and parent_interview.interview_mode == "assessment"
    if immutable and question_attempt.answer_saved_at is not None and not same_answer:
        raise fastapi.HTTPException(409, "This assessment answer is already committed. Continue with the saved answer.")
    if same_answer:
        stored_attempt = question_attempt
        audio_url = str(question_attempt.audio_url)
    else:
        try:
            audio_url = await upload_answer_audio_to_s3(
                audio_bytes=audio_bytes,
                user_id=current_user.id,
                question_attempt_id=question_attempt_id_int,
                extension=Path(file_metadata["filename"]).suffix.lower() or ".wav",
                checksum=checksum,
                content_type=file_metadata["content_type"],
            )
        except Exception:  # storage details must never leak to the student
            audio_url = None
        if not audio_url:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Your answer is still on this device but could not be stored. Retry the upload.",
                headers={"Retry-After": "3"},
            )
        # Acquire the interview row immediately before the durable answer
        # commit. A concurrent takeover must wait for this transaction, so an
        # old device can never pass validation and save after being replaced.
        await _require_active_interview(
            question_attempt,
            interview_repo,
            lease_token=session_lease_token,
            user_id=current_user.id,
            lock=True,
        )
        try:
            saved_attempt = await question_repo.save_audio_asset(
                question_attempt_id=question_attempt_id_int,
                audio_url=audio_url,
                checksum=checksum,
                **({"immutable": immutable, "follow_up_expected": False} if fixed_plan else {}),
            )
        except ValueError as exc:
            await question_repo.async_session.rollback()
            if str(exc) == "ASSESSMENT_ANSWER_COMMITTED":
                raise fastapi.HTTPException(409, "This assessment answer is already committed. Reload the saved checkpoint.") from exc
            saved_attempt = None
        except Exception:
            await question_repo.async_session.rollback()
            saved_attempt = None
        if saved_attempt is None:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Your answer is still on this device but could not be committed. Retry the upload.",
                headers={"Retry-After": "3"},
            )
        stored_attempt = saved_attempt

    current_status = (
        stored_attempt.transcription_status or TranscriptionStatus.PENDING.value
    )
    if same_answer and current_status == TranscriptionStatus.TRANSCRIBED.value:
        existing_transcription = question_attempt.transcription
        follow_up_payload = await question_repo.get_follow_up_payload_for_attempt(
            question_attempt_id=question_attempt_id_int
        )
        existing_follow_up = (
            FollowUpQuestion.model_validate(follow_up_payload)
            if follow_up_payload is not None
            else None
        )
        return AudioTranscriptionResponse(
            question_attempt_id=question_attempt_id_int,
            filename=file_metadata["filename"],
            content_type=file_metadata["content_type"],
            size=file_metadata["size"],
            duration_seconds=duration_seconds,
            audio_url=audio_url,
            transcription=strip_word_level_data(existing_transcription),
            word_count=extract_word_count(existing_transcription),
            whisper_model=None,
            whisper_latency_ms=0,
            whisper_error=None,
            message="Answer was already saved.",
            saved=True,
            save_error=None,
            follow_up_generated=existing_follow_up is not None,
            follow_up_question=existing_follow_up,
            answer_version=max(1, question_attempt.answer_version),
            transcription_status="transcribed",
        )

    # A failed job is explicitly retried by resubmitting the same answer. A
    # pending answer is also safe to re-enqueue: the worker claims it with a
    # compare-and-set transition, so duplicate browser requests cannot trigger
    # duplicate model calls.
    should_enqueue = not same_answer or current_status in {
        TranscriptionStatus.PENDING.value,
        TranscriptionStatus.FAILED.value,
    }
    if current_status == TranscriptionStatus.FAILED.value:
        reset_attempt = await question_repo.mark_transcription_pending(
            question_attempt_id=question_attempt_id_int,
            checksum=checksum,
        )
        if reset_attempt is not None:
            stored_attempt = reset_attempt
            current_status = TranscriptionStatus.PENDING.value

    if should_enqueue:
        job_id = await enqueue_job(
            "process_answer_transcription_task",
            question_attempt_id=question_attempt_id_int,
            checksum=checksum,
            language=validated_language,
            filename=file_metadata["filename"],
        )
        if not job_id:
            try:
                failed_attempt = await question_repo.mark_transcription_failed(
                    question_attempt_id=question_attempt_id_int,
                    checksum=checksum,
                    error=(
                        "Your answer is saved, but speech feedback could not be started. "
                        "Select Retry speech feedback."
                    ),
                )
            except Exception:
                await question_repo.async_session.rollback()
                failed_attempt = None
            if failed_attempt is not None:
                stored_attempt = failed_attempt
            current_status = TranscriptionStatus.FAILED.value
        else:
            current_status = TranscriptionStatus.PENDING.value

    final_saved = stored_attempt is not None and bool(stored_attempt.audio_url)
    message = (
        "Answer saved. Speech feedback is being prepared."
        if current_status != TranscriptionStatus.FAILED.value
        else "Answer saved. Speech feedback could not be started; you can retry it."
    )
    logger.info(
        "Audio answer accepted | QuestionAttempt=%s | TotalTime=%.2fs | Saved=%s | Status=%s",
        question_attempt_id_int,
        time.perf_counter() - request_start,
        final_saved,
        current_status,
    )
    return AudioTranscriptionResponse(
        question_attempt_id=question_attempt_id_int,
        filename=file_metadata["filename"],
        content_type=file_metadata["content_type"],
        size=file_metadata["size"],
        duration_seconds=duration_seconds,
        audio_url=audio_url,
        transcription=None,
        word_count=None,
        whisper_model=None,
        whisper_latency_ms=None,
        whisper_error=None,
        message=message,
        saved=final_saved,
        save_error=None,
        follow_up_generated=False,
        follow_up_metadata=None,
        follow_up_question=None,
        answer_version=max(1, stored_attempt.answer_version if stored_attempt else 1),
        transcription_status=current_status,
    )


@router.post(
    path="/answers/text",
    name="audio:save-text-answer",
    status_code=fastapi.status.HTTP_410_GONE,
    summary="Reject the retired typed-answer mode",
    include_in_schema=False,
)
async def save_text_answer(
    _payload: dict,
    current_user=fastapi.Depends(get_current_user),
) -> None:
    """Fail cached clients closed after typed interview answers were retired.

    Historical text answers remain readable for existing reports, but new
    interview evidence must come from a durably saved audio recording.
    """
    del current_user
    raise fastapi.HTTPException(
        status_code=fastapi.status.HTTP_410_GONE,
        detail=(
            "Typed interview answers are no longer supported. "
            "Record an audio answer or skip this question."
        ),
    )


@router.get(
    path="/audio/attempts/{question_attempt_id}/transcription",
    name="audio:transcription-status",
    response_model=TranscriptionStatusResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Progress of an answer being transcribed in the background",
)
async def get_transcription_status(
    question_attempt_id: int,
    current_user=fastapi.Depends(get_current_user),
    question_repo: QuestionAttemptCRUDRepository = fastapi.Depends(
        get_repository(repo_type=QuestionAttemptCRUDRepository)
    ),
) -> TranscriptionStatusResponse:
    """Lets a client follow an answer that is transcribed outside the request.

    Also makes the flow resumable: a student who reloads mid-transcription can
    reattach to the work already in progress instead of losing the answer, which
    the synchronous path could not offer.

    Scoped by owner, and a foreign attempt is reported as missing rather than
    forbidden so the endpoint cannot be used to discover which ids exist.
    """
    attempt = await question_repo.get_by_id_and_user(
        question_attempt_id=question_attempt_id, user_id=current_user.id
    )
    if not attempt:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Question attempt not found or access denied",
        )

    status = attempt.transcription_status or TranscriptionStatus.PENDING.value
    transcription = attempt.transcription
    evidence = (
        transcription.get("evidence")
        if isinstance(transcription, dict) and isinstance(transcription.get("evidence"), dict)
        else {}
    )
    follow_up_question: FollowUpQuestion | None = None
    if status == TranscriptionStatus.TRANSCRIBED.value and hasattr(
        question_repo, "get_follow_up_payload_for_attempt"
    ):
        follow_up_payload = await question_repo.get_follow_up_payload_for_attempt(
            question_attempt_id=attempt.id
        )
        if follow_up_payload is not None:
            follow_up_question = FollowUpQuestion.model_validate(follow_up_payload)
    return TranscriptionStatusResponse(
        question_attempt_id=attempt.id,
        status=status,
        is_terminal=status in TERMINAL_TRANSCRIPTION_STATUSES,
        # The recording is stored before the job is queued, so an answer in
        # flight is already safe. Reporting otherwise would tell a student their
        # answer was lost while it is merely still being processed.
        saved=bool(attempt.audio_url or attempt.transcription),
        answer_version=max(0, attempt.answer_version or 0),
        transcription=strip_word_level_data(transcription) if transcription else None,
        word_count=extract_word_count(transcription) if transcription else None,
        error=attempt.transcription_error,
        evidence_status=evidence.get("status"),
        evidence_reason=evidence.get("reason"),
        retryable=evidence.get("status") != "unavailable",
        follow_up_question=follow_up_question,
        updated_at=(
            attempt.transcription_updated_at.isoformat()
            if attempt.transcription_updated_at
            else None
        ),
    )


@router.post(
    path="/audio/attempts/{question_attempt_id}/transcription/retry",
    name="audio:retry-transcription",
    response_model=TranscriptionStatusResponse,
    status_code=fastapi.status.HTTP_202_ACCEPTED,
    summary="Retry speech processing for a saved answer",
)
async def retry_transcription(
    question_attempt_id: int,
    current_user=fastapi.Depends(get_current_user),
    question_repo: QuestionAttemptCRUDRepository = fastapi.Depends(
        get_repository(repo_type=QuestionAttemptCRUDRepository)
    ),
) -> TranscriptionStatusResponse:
    """Requeue AI work without requiring the interview to remain active."""
    attempt = await question_repo.get_by_id_and_user(
        question_attempt_id=question_attempt_id, user_id=current_user.id
    )
    if attempt is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Question attempt not found or access denied",
        )
    if (
        attempt.answer_kind != "audio"
        or not attempt.audio_url
        or not attempt.answer_checksum
    ):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT,
            detail="This question does not have a saved audio answer to process.",
        )
    if attempt.transcription_status == TranscriptionStatus.TRANSCRIBED.value:
        return await get_transcription_status(
            question_attempt_id=question_attempt_id,
            current_user=current_user,
            question_repo=question_repo,
        )
    evidence = (
        attempt.transcription.get("evidence")
        if isinstance(attempt.transcription, dict)
        and isinstance(attempt.transcription.get("evidence"), dict)
        else {}
    )
    if evidence.get("status") == "unavailable":
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT,
            detail=(
                "This recording does not contain enough clear speech to reprocess. "
                "Record the answer again."
            ),
        )

    # Do not duplicate an active model call. A processing row becomes eligible
    # for recovery only after the worker's five-minute timeout window, covering
    # a killed worker without charging for two simultaneous transcriptions.
    processing_updated_at = attempt.transcription_updated_at
    if (
        attempt.transcription_status == TranscriptionStatus.PROCESSING.value
        and processing_updated_at is not None
        and datetime.datetime.now(datetime.timezone.utc) - processing_updated_at
        < datetime.timedelta(minutes=5)
    ):
        return await get_transcription_status(
            question_attempt_id=question_attempt_id,
            current_user=current_user,
            question_repo=question_repo,
        )

    pending = await question_repo.mark_transcription_pending(
        question_attempt_id=attempt.id,
        checksum=attempt.answer_checksum,
    )
    if pending is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT,
            detail="This answer changed while speech feedback was being retried.",
        )
    job_id = await enqueue_job(
        "process_answer_transcription_task",
        question_attempt_id=attempt.id,
        checksum=attempt.answer_checksum,
        language=(attempt.transcription or {}).get("language", "en"),
        filename=f"answer-{attempt.id}{Path(attempt.audio_url).suffix or '.wav'}",
    )
    if not job_id:
        await question_repo.mark_transcription_failed(
            question_attempt_id=attempt.id,
            checksum=attempt.answer_checksum,
            error=(
                "Your answer is saved, but speech feedback could not be started. "
                "Select Retry speech feedback."
            ),
        )
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Your answer is saved, but speech feedback could not be started. Try again.",
            headers={"Retry-After": "3"},
        )
    return TranscriptionStatusResponse(
        question_attempt_id=pending.id,
        status=TranscriptionStatus.PENDING.value,
        is_terminal=False,
        saved=True,
        answer_version=max(0, pending.answer_version or 0),
        updated_at=(
            pending.transcription_updated_at.isoformat()
            if pending.transcription_updated_at
            else None
        ),
    )
