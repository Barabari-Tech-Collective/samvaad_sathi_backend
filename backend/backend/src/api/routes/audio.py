import fastapi
import time
import logging
import hashlib
from pathlib import Path

from fastapi import Form, UploadFile, File

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.repository import get_repository
from src.models.schemas.audio import (
    AudioTranscriptionResponse,
    TextAnswerRequest,
    TextAnswerResponse,
    TranscriptionStatusResponse,
)
from src.models.db.question_attempt import (
    TERMINAL_TRANSCRIPTION_STATUSES,
    TranscriptionStatus,
)
from src.repository.crud.interview import InterviewCRUDRepository
from src.repository.crud.question import QuestionAttemptCRUDRepository
from src.services.audio_processor import validate_audio_file, get_audio_duration_estimate
from src.services.whisper import transcribe_audio_with_whisper, validate_transcription_language, extract_word_count, strip_word_level_data
from src.services.follow_up import FollowUpService
from src.services.interview_lifecycle import interview_has_expired
from src.services.s3_service import upload_answer_audio_to_s3


router = fastapi.APIRouter(prefix="", tags=["audio"])
logger = logging.getLogger(__name__)


async def _require_active_interview(question_attempt, interview_repo) -> None:
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


@router.post(
    path="/transcribe-whisper",
    name="audio:transcribe-whisper", 
    response_model=AudioTranscriptionResponse,
    status_code=fastapi.status.HTTP_202_ACCEPTED,
    summary="Upload audio answer and transcribe with Whisper",
    description=(
        "Accepts audio files (.mp3, .wav, .m4a, .flac) up to 25MB and transcribes them using OpenAI Whisper API. "
        "Returns word-level transcription with timestamps. Requires valid question_attempt_id that belongs to the authenticated user."
    ),
)
async def transcribe_audio_answer(
    question_attempt_id: str = Form(..., description="ID of the question attempt this audio answer belongs to"),
    language: str = Form(default="en", description="Language code for transcription (e.g., 'en', 'es', 'fr')"),
    file: UploadFile = File(..., description="Audio file to transcribe. Supported: .mp3, .wav, .m4a, .flac (max 25MB)"),
    current_user = fastapi.Depends(get_current_user),
    question_repo: QuestionAttemptCRUDRepository = fastapi.Depends(get_repository(repo_type=QuestionAttemptCRUDRepository)),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
) -> AudioTranscriptionResponse:
    """
    Main endpoint for audio transcription workflow:
    1. Validate user owns the question attempt
    2. Validate and process audio file
    3. Transcribe with Whisper API
    4. Save audio file and update database
    5. Return transcription results
    """
    # Step 1: Verify question attempt exists and belongs to current user
    try:
        request_start = time.perf_counter()
        question_attempt_id_int = int(question_attempt_id)
    except ValueError:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid question_attempt_id format. Must be a valid integer."
        )
    logger.info(
       "Transcription request started | User=%s | QuestionAttempt=%s | File=%s | Language=%s",
        current_user.id,
        question_attempt_id,
        file.filename,
        language,
    )
    question_attempt = await question_repo.get_by_id_and_user(
        question_attempt_id=question_attempt_id_int,
        user_id=current_user.id
    )

    logger.info(
       "Question attempt verified | User=%s | QuestionAttempt=%s",
       current_user.id,
       question_attempt_id_int,
    )
    
    if not question_attempt:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Question attempt not found or access denied"
        )

    # A terminated or completed interview must not accept a late answer for a
    # question attempt that was already created before it ended - otherwise a
    # proctoring termination only locks the student out of *starting* new
    # questions, not from finishing the one they were on when they left.
    await _require_active_interview(question_attempt, interview_repo)

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
            detail=f"Audio file validation failed: {str(e)}"
        )

    # Step 3: Estimate audio duration
    duration_seconds = get_audio_duration_estimate(audio_bytes, file_metadata["content_type"])

    # Step 4: Validate and normalize language
    validated_language = validate_transcription_language(language)
    # Persist the recording before any external AI call. The upload response may
    # take longer while synchronous transcription remains in place, but a
    # Whisper outage can no longer turn a valid answer into data loss.
    checksum = hashlib.sha256(audio_bytes).hexdigest()
    same_answer = (
        question_attempt.answer_checksum == checksum
        and question_attempt.audio_url
        and question_attempt.answer_kind == "audio"
    )
    if same_answer:
        stored_attempt = question_attempt
        audio_url = question_attempt.audio_url
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
        try:
            stored_attempt = await question_repo.save_audio_asset(
                question_attempt_id=question_attempt_id_int,
                audio_url=audio_url,
                checksum=checksum,
            )
        except Exception:
            await question_repo.async_session.rollback()
            stored_attempt = None
        if stored_attempt is None:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Your answer is still on this device but could not be committed. Retry the upload.",
                headers={"Retry-After": "3"},
            )

    if same_answer and question_attempt.transcription_status == "transcribed":
        existing_transcription = question_attempt.transcription
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
            answer_version=max(1, question_attempt.answer_version),
            transcription_status="transcribed",
        )

    whisper_start = time.perf_counter()
    transcription, whisper_error, whisper_latency_ms, whisper_model = await transcribe_audio_with_whisper(
        audio_bytes=audio_bytes,
        filename=file_metadata["filename"],
        language=validated_language,
    )
    logger.info(
        "Whisper completed | QuestionAttempt=%s | Time=%.2fs | Error=%s | WordCount=%d",
        question_attempt_id_int,
        time.perf_counter() - whisper_start,
        whisper_error,
        extract_word_count(transcription) or 0,
    )

    transcription_status = "failed" if whisper_error or not transcription else "transcribed"
    if transcription_status == "transcribed":
        try:
            transcribed_attempt = await question_repo.update_audio_transcription(
                question_attempt_id=question_attempt_id_int,
                audio_url=audio_url,
                transcription=transcription,
                checksum=checksum,
            )
            if transcribed_attempt is not None:
                stored_attempt = transcribed_attempt
            else:
                transcription_status = "failed"
        except Exception:
            await question_repo.async_session.rollback()
            transcription_status = "failed"
    if transcription_status == "failed":
        try:
            failed_attempt = await question_repo.mark_transcription_failed(
                question_attempt_id=question_attempt_id_int,
                checksum=checksum,
                error="Detailed feedback is delayed because speech processing failed. Your answer is saved.",
            )
            if failed_attempt is not None:
                stored_attempt = failed_attempt
        except Exception:
            await question_repo.async_session.rollback()

    # From here onward `saved` means the student's answer is durable, not that
    # every downstream AI operation happened to succeed.
    final_saved = stored_attempt is not None and bool(stored_attempt.audio_url)
    final_save_error = None
    word_count = extract_word_count(transcription)
    follow_up_service = FollowUpService(async_session=question_repo.async_session)
    
    follow_up_metadata = None
    follow_up_question = None
    if final_saved and transcription_status == "transcribed":
        try:
            followup_start = time.perf_counter()

            logger.info(
               "Follow-up generation started | QuestionAttempt=%s",
               question_attempt_id_int,
            )
            follow_up_metadata = await follow_up_service.handle_transcription_saved(question_attempt_id_int)
            if follow_up_metadata:
                qid = follow_up_metadata.get("follow_up_question_id")
                attempt_id = follow_up_metadata.get("follow_up_question_attempt_id")
                text = follow_up_metadata.get("follow_up_question_text")
                if qid and attempt_id and text:
                    follow_up_question = {
                        "interview_question_id": qid,
                        "question_attempt_id": attempt_id,
                        "parent_question_id": follow_up_metadata.get("parent_question_id"),
                        "text": text,
                        "strategy": follow_up_metadata.get("strategy"),
                    }
                    logger.info(
                       "Follow-up generation finished | QuestionAttempt=%s | Time=%.2fs | Generated=%s",
                       question_attempt_id_int,
                       time.perf_counter() - followup_start,
                       follow_up_metadata is not None,
                    )
        except Exception as follow_up_error:  # noqa: BLE001
            logger.warning("Failed to generate follow-up question for attempt %s: %s", question_attempt_id_int, follow_up_error)
            logger.exception(
              "Follow-up generation failed | QuestionAttempt=%s",
              question_attempt_id_int,
            )

    # Generate status message
    if final_saved and transcription_status == "transcribed":
        message = "Answer saved and transcribed successfully"
    elif final_saved:
        message = "Answer saved. Detailed feedback is taking longer than usual."
    else:
        message = "Audio upload failed"
    logger.info(
        "Transcription workflow completed | QuestionAttempt=%s | TotalTime=%.2fs | Saved=%s | FollowUp=%s",
        question_attempt_id_int,
        time.perf_counter() - request_start,
        final_saved,
        follow_up_metadata is not None,
    )
    return AudioTranscriptionResponse(
        question_attempt_id=question_attempt_id_int,
        filename=file_metadata["filename"],
        content_type=file_metadata["content_type"],
        size=file_metadata["size"],
        duration_seconds=duration_seconds,
        audio_url=audio_url,
        transcription=strip_word_level_data(transcription),
        word_count=word_count,
        whisper_model=whisper_model,
        whisper_latency_ms=whisper_latency_ms,
        whisper_error=whisper_error,
        message=message,
        saved=final_saved,
        save_error=final_save_error,
        follow_up_generated=follow_up_metadata is not None,
        follow_up_metadata=follow_up_metadata,
        follow_up_question=follow_up_question,
        answer_version=max(1, stored_attempt.answer_version if stored_attempt else 1),
        transcription_status=transcription_status,
    )


@router.post(
    path="/answers/text",
    name="audio:save-text-answer",
    response_model=TextAnswerResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Save a typed interview answer",
)
async def save_text_answer(
    payload: TextAnswerRequest,
    current_user=fastapi.Depends(get_current_user),
    question_repo: QuestionAttemptCRUDRepository = fastapi.Depends(
        get_repository(repo_type=QuestionAttemptCRUDRepository)
    ),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
) -> TextAnswerResponse:
    attempt = await question_repo.get_by_id_and_user(
        question_attempt_id=payload.question_attempt_id, user_id=current_user.id
    )
    if attempt is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Question attempt not found or access denied",
        )
    await _require_active_interview(attempt, interview_repo)
    normalized = " ".join(payload.text.split())
    if not normalized:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Type an answer before saving.",
        )
    try:
        saved = await question_repo.save_text_answer(
            question_attempt_id=attempt.id,
            text=normalized,
            language=validate_transcription_language(payload.language),
        )
    except Exception:
        await question_repo.async_session.rollback()
        saved = None
    if saved is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Your typed answer could not be saved. Keep this page open and retry.",
        )
    return TextAnswerResponse(
        question_attempt_id=saved.id,
        answer_version=max(1, saved.answer_version),
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
    return TranscriptionStatusResponse(
        question_attempt_id=attempt.id,
        status=status,
        is_terminal=status in TERMINAL_TRANSCRIPTION_STATUSES,
        # The recording is stored before the job is queued, so an answer in
        # flight is already safe. Reporting otherwise would tell a student their
        # answer was lost while it is merely still being processed.
        saved=bool(attempt.audio_url or attempt.transcription),
        transcription=strip_word_level_data(transcription) if transcription else None,
        word_count=extract_word_count(transcription) if transcription else None,
        error=attempt.transcription_error,
        updated_at=(
            attempt.transcription_updated_at.isoformat()
            if attempt.transcription_updated_at
            else None
        ),
    )
