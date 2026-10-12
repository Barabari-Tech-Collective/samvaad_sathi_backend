import pydantic

from src.models.schemas.base import BaseSchemaModel


class FollowUpQuestion(BaseSchemaModel):
    """Payload for a generated follow-up question (returned inline)."""
    interview_question_id: int = pydantic.Field(description="ID of the generated follow-up question")
    question_attempt_id: int = pydantic.Field(description="Attempt ID linked to the follow-up question")
    parent_question_id: int | None = pydantic.Field(default=None, description="ID of the parent question")
    text: str = pydantic.Field(description="Follow-up question text")
    strategy: str | None = pydantic.Field(default=None, description="Follow-up strategy used for generation")


class AudioTranscriptionResponse(BaseSchemaModel):
    """Response model for audio transcription endpoint"""
    question_attempt_id: int = pydantic.Field(description="ID of the question attempt this audio belongs to")
    filename: str = pydantic.Field(description="Original uploaded audio filename")
    content_type: str = pydantic.Field(description="MIME type of the uploaded audio file")
    size: int = pydantic.Field(description="Audio file size in bytes (<= 25MB)")
    duration_seconds: float | None = pydantic.Field(default=None, description="Audio duration in seconds")
    audio_url: str = pydantic.Field(description="Stored audio file URL/path")
    transcription: dict | None = pydantic.Field(default=None, description="Whisper transcription with word-level timestamps")
    word_count: int | None = pydantic.Field(default=None, description="Number of words in transcription")
    whisper_model: str | None = pydantic.Field(default=None, description="Whisper model used for transcription")
    whisper_latency_ms: int | None = pydantic.Field(default=None, description="Whisper API call latency in milliseconds")
    whisper_error: str | None = pydantic.Field(default=None, description="Whisper API error message if transcription failed")
    message: str = pydantic.Field(description="Human-friendly status message")
    saved: bool = pydantic.Field(description="Whether the audio and transcription were successfully saved")
    save_error: str | None = pydantic.Field(default=None, description="Error message if save operation failed")
    follow_up_generated: bool = pydantic.Field(default=False, description="Whether a follow-up question was generated")
    follow_up_metadata: dict | None = pydantic.Field(default=None, description="Metadata about the generated follow-up question, if any")
    follow_up_question: FollowUpQuestion | None = pydantic.Field(
        default=None,
        description="Generated follow-up question (if created) with IDs and text",
    )


class TranscriptionStatusResponse(BaseSchemaModel):
    """What a client polls while its answer is transcribed in the background.

    `status` is the only field a caller must branch on. It reaches `transcribed`
    or `failed` exactly once and never changes afterwards, so polling can stop
    the moment `is_terminal` is true.
    """

    question_attempt_id: int = pydantic.Field(description="Attempt this status belongs to")
    status: str = pydantic.Field(
        description="pending, processing, transcribed, or failed"
    )
    is_terminal: bool = pydantic.Field(
        description="True when the status will not change again and polling should stop"
    )
    saved: bool = pydantic.Field(
        description="Whether the answer is durably stored. True from acceptance onward, "
                    "so a student is never told their recording was lost while it is still being processed"
    )
    transcription: dict | None = pydantic.Field(
        default=None, description="Populated once status is transcribed"
    )
    word_count: int | None = pydantic.Field(default=None, description="Words in the transcription")
    error: str | None = pydantic.Field(
        default=None, description="Shown to the student when status is failed"
    )
    follow_up_question: FollowUpQuestion | None = pydantic.Field(
        default=None,
        description="Follow-up generated from this answer, if any. Arrives with the "
                    "terminal status rather than with the upload response",
    )
    updated_at: str | None = pydantic.Field(
        default=None, description="When the status last changed, ISO 8601"
    )


class AudioValidationError(BaseSchemaModel):
    """Error response for audio validation failures"""
    error: str = pydantic.Field(description="Error message")
    details: str | None = pydantic.Field(default=None, description="Additional error details")
    supported_formats: list[str] = pydantic.Field(
        default=["audio/mpeg", "audio/wav", "audio/mp4", "audio/flac", "audio/x-flac"],
        description="List of supported audio MIME types"
    )
    max_size_mb: int = pydantic.Field(default=25, description="Maximum allowed file size in MB")


class TranscriptionRequest(BaseSchemaModel):
    """Request model for audio transcription"""
    question_attempt_id: int = pydantic.Field(description="ID of the question attempt")
    language: str = pydantic.Field(default="en", description="Language code for transcription (ISO 639-1)")
