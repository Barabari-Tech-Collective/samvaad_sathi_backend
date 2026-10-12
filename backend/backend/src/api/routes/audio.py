import fastapi
import time
import logging
from pathlib import Path

from fastapi import Form, UploadFile, File

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.repository import get_repository
from src.models.schemas.audio import AudioTranscriptionResponse, TranscriptionStatusResponse
from src.models.db.question_attempt import (
    TERMINAL_TRANSCRIPTION_STATUSES,
    TranscriptionStatus,
)
from src.repository.crud.question import QuestionAttemptCRUDRepository
from src.services.audio_processor import validate_audio_file, save_audio_file, get_audio_duration_estimate, cleanup_temp_audio_file
from src.services.whisper import transcribe_audio_with_whisper, validate_transcription_language, extract_word_count, strip_word_level_data
from src.services.follow_up import FollowUpService
from src.services.s3_service import upload_answer_audio_to_s3


router = fastapi.APIRouter(prefix="", tags=["audio"])
logger = logging.getLogger(__name__)


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
    whisper_start = time.perf_counter()
    logger.info(
      "Whisper transcription started | QuestionAttempt=%s",
      question_attempt_id_int,
    )
    # Step 5: Transcribe with Whisper API
    transcription, whisper_error, whisper_latency_ms, whisper_model = await transcribe_audio_with_whisper(
        audio_bytes=audio_bytes,
        filename=file_metadata["filename"], 
        language=validated_language
    )
    logger.info(
    "Whisper completed | QuestionAttempt=%s | Time=%.2fs | Error=%s | WordCount=%d",
      question_attempt_id_int,
      time.perf_counter() - whisper_start,
      whisper_error,
      extract_word_count(transcription) or 0,
    )

    follow_up_service = FollowUpService(async_session=question_repo.async_session)

    # Step 6: Create temporary file for processing
    temp_file_path = ""
    audio_url = ""
    save_error = None
    try:
        save_audio_start = time.perf_counter()
        temp_file_path, audio_url = await save_audio_file(
            audio_bytes=audio_bytes,
            filename=file_metadata["filename"],
            user_id=current_user.id,
            question_attempt_id=question_attempt_id_int
        )
        logger.info(
        "Audio file saved | QuestionAttempt=%s | Time=%.2fs | AudioURL=%s",
        question_attempt_id_int,
        time.perf_counter() - save_audio_start,
        audio_url,
        )
        # save_audio_file only produces a temporary file and a reference name,
        # and the temporary file is deleted before this request returns. Stored
        # on its own, the recording would be unrecoverable: a failed
        # transcription would lose the answer for good, it could never be
        # re-transcribed after a provider outage, and a disputed score could not
        # be checked against what the student actually said. It is also what
        # lets transcription move off the request path later - a background
        # worker needs something to fetch.
        stored_key = await upload_answer_audio_to_s3(
            audio_bytes=audio_bytes,
            user_id=current_user.id,
            question_attempt_id=question_attempt_id_int,
            extension=Path(file_metadata["filename"]).suffix.lower() or ".wav",
        )
        if stored_key:
            audio_url = stored_key
        else:
            # Transcription still runs from the temporary file below, so the
            # answer is not lost right now - only its recoverability is. Worth
            # an error rather than failing a student's upload over it.
            logger.error(
                "Answer audio was not stored durably | QuestionAttempt=%s | "
                "Transcription will proceed but the recording cannot be recovered",
                question_attempt_id_int,
            )
    except Exception as e:
        save_error = str(e)

    # Step 7: Update question attempt in database (only metadata, no file storage)
    saved = False
    db_save_error = None
    if audio_url and transcription and not save_error:
        try:
            db_start = time.perf_counter()
            updated_qa = await question_repo.update_audio_transcription(
                question_attempt_id=question_attempt_id_int,
                audio_url=audio_url,  # Just a reference name, not a real file path
                transcription=transcription
            )
            saved = updated_qa is not None
            logger.info(
               "Database updated | QuestionAttempt=%s | Time=%.2fs | Saved=%s",
               question_attempt_id_int,
               time.perf_counter() - db_start,
               updated_qa is not None,
            )
        except Exception as e:
            db_save_error = str(e)
            saved = False

    # Step 8: Clean up temporary file
    if temp_file_path:
        try:
            await cleanup_temp_audio_file(temp_file_path)
        except Exception:
            # Cleanup cannot turn a committed answer into an apparent save failure.
            logger.warning("Temporary audio cleanup failed | QuestionAttempt=%s", question_attempt_id_int)

    # Step 9: Extract metadata for response
    word_count = extract_word_count(transcription)
    
    # Determine overall save status and error message
    final_save_error = save_error or db_save_error
    final_saved = saved and not final_save_error

    if not final_saved:
        await question_repo.async_session.rollback()
        logger.error("Answer persistence failed | QuestionAttempt=%s", question_attempt_id_int)
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Your answer was not saved. Keep this page open and retry the upload.",
            headers={"Retry-After": "3"},
        )
    
    follow_up_metadata = None
    follow_up_question = None
    if final_saved:
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
    if final_saved:
        message = "Audio uploaded and transcribed successfully"
    elif transcription and not whisper_error:
        message = "Audio transcribed but failed to save"
    elif whisper_error:
        message = f"Audio uploaded but transcription failed: {whisper_error}"
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
        saved=bool(attempt.audio_url),
        transcription=strip_word_level_data(transcription) if transcription else None,
        word_count=extract_word_count(transcription) if transcription else None,
        error=attempt.transcription_error,
        updated_at=(
            attempt.transcription_updated_at.isoformat()
            if attempt.transcription_updated_at
            else None
        ),
    )
