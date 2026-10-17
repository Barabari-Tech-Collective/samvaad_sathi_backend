from __future__ import annotations

import datetime
from typing import Literal

import pydantic

from src.models.schemas.base import BaseSchemaModel


class InterviewQuestionOut(BaseSchemaModel):
    interview_question_id: int = pydantic.Field(
        description="Unique identifier for the interview question"
    )
    text: str
    topic: str | None = None
    category: str | None = None
    status: str
    resume_used: bool
    is_follow_up: bool = pydantic.Field(
        default=False, description="Whether this question is a follow-up prompt"
    )
    parent_question_id: int | None = pydantic.Field(
        default=None, description="Parent question ID if this is a follow-up"
    )
    follow_up_strategy: str | None = pydantic.Field(
        default=None, description="Strategy used for follow-up generation"
    )
    supplement: "QuestionSupplementOut | None" = pydantic.Field(
        default=None,
        description="Optional supplemental snippet (diagram or code) for this question",
    )


class ResumeQuestionOut(InterviewQuestionOut):
    """One immutable question plus the latest durable answer checkpoint."""

    question_attempt_id: int | None = None
    answer_saved: bool = False
    answer_version: int = pydantic.Field(default=0, ge=0)
    answer_kind: Literal["audio", "text"] | None = None
    transcription_status: (
        Literal["pending", "processing", "transcribed", "failed"] | None
    ) = None
    attempt_state: Literal["in_progress", "answer_saved", "skipped"] | None = None
    skipped: bool = False
    follow_up_disposition: (
        Literal["pending", "generated", "not_generated", "not_applicable", "skipped"]
        | None
    ) = None


class CreateAttemptResponse(BaseSchemaModel):
    question_attempt_id: int
    created: bool = pydantic.Field(
        default=True,
        description="False when an idempotent retry returned the existing attempt",
    )
    state_version: int = pydantic.Field(default=0, ge=0)
    current_question_id: int | None = None


class CreateAttemptRequest(BaseSchemaModel):
    interview_id: int
    question_id: int
    client_attempt_id: str | None = pydantic.Field(
        default=None,
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="Stable client idempotency key for this interview question",
    )
    session_lease_token: str | None = pydantic.Field(
        default=None, min_length=20, max_length=256
    )

    # Swagger example
    model_config = BaseSchemaModel.model_config.copy()
    model_config["json_schema_extra"] = {
        "examples": [{"interviewId": 123, "questionId": 456}]
    }


class QuestionItem(BaseSchemaModel):
    interview_question_id: int | None = None
    text: str
    topic: str | None = None
    difficulty: str | None = None
    category: str | None = None
    is_follow_up: bool = False
    parent_question_id: int | None = None
    follow_up_strategy: str | None = None
    supplement: "QuestionSupplementOut | None" = None
    audio_url: str | None = None


class QuestionItemWithHint(BaseSchemaModel):
    """Question item with structure hint for practice mode"""

    interview_question_id: int | None = None
    text: str
    topic: str | None = None
    difficulty: str | None = None
    category: str | None = None
    is_follow_up: bool = False
    parent_question_id: int | None = None
    follow_up_strategy: str | None = None
    supplement: "QuestionSupplementOut | None" = None
    structure_hint: str = pydantic.Field(
        description="1-2 line hint about expected answer structure"
    )


class QuestionSupplementOut(BaseSchemaModel):
    question_id: int = pydantic.Field(
        description="Interview question ID this supplement belongs to"
    )
    supplement_type: str = pydantic.Field(
        description="Type of supplement: 'code' or 'diagram'"
    )
    format: str | None = pydantic.Field(
        default=None, description="Language/format such as 'python' or 'mermaid'"
    )
    content: str = pydantic.Field(description="Renderable snippet content")


class InterviewCreate(BaseSchemaModel):
    track: str
    difficulty: str | None = None  # easy | medium | hard
    job_profile_id: int | None = None


class GenerateQuestionsRequest(BaseSchemaModel):
    interview_id: int | None = None
    use_resume: bool = True  # Whether to use resume text for question generation

    # Swagger example
    model_config = BaseSchemaModel.model_config.copy()
    model_config["json_schema_extra"] = {
        "examples": [{"useResume": True}, {"interviewId": 123, "useResume": True}]
    }


class GenerateNonTechQuestionsRequest(BaseSchemaModel):
    job_profile_id: int
    difficulty: str | None = None
    use_resume: bool = True


class InterviewInResponse(BaseSchemaModel):
    interview_id: int = pydantic.Field(
        description="Unique identifier for the interview"
    )
    track: str
    difficulty: str
    status: str
    created_at: datetime.datetime
    resumed: bool
    paused_at: datetime.datetime | None = None
    expires_at: datetime.datetime | None = None
    interview_mode: str = pydantic.Field(
        default="practice",
        description=(
            "Snapshotted from the job profile at creation, or 'practice' for an "
            "interview with none. Does not change if the profile's mode changes later."
        ),
    )


class GeneratedQuestionsInResponse(BaseSchemaModel):
    interview_id: int
    track: str
    count: int
    questions: list[str]
    question_ids: list[int] | None = (
        None  # Include question IDs for consistency with get-questions endpoint
    )
    items: list[QuestionItem] | None = None
    cached: bool | None = None
    llm_model: str | None = None
    llm_latency_ms: int | None = None
    llm_error: str | None = None
    interview_mode: str = pydantic.Field(
        default="practice",
        description="Snapshotted on the interview at creation. See Interview.interview_mode.",
    )


class StructurePracticeQuestionsResponse(BaseSchemaModel):
    """Response for structure practice mode with hints"""

    interview_id: int | None = None
    track: str
    count: int
    questions: list[str]
    question_ids: list[int] | None = None
    items: list[QuestionItemWithHint]
    llm_model: str | None = None
    llm_latency_ms: int | None = None
    llm_error: str | None = None
    cached: bool = False


class InterviewItem(BaseSchemaModel):
    interview_id: int = pydantic.Field(
        description="Unique identifier for the interview"
    )
    track: str
    difficulty: str
    status: str
    created_at: datetime.datetime
    knowledge_percentage: float | None = pydantic.Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Knowledge competence percentage from summary report",
    )
    speech_fluency_percentage: float | None = pydantic.Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Speech fluency percentage from summary report",
    )
    attempts_count: int = pydantic.Field(
        default=0,
        ge=0,
        description="Number of summary reports/attempts for this interview",
    )
    resume_used: bool = pydantic.Field(
        default=False, description="Whether resume was used for question generation"
    )


class InterviewItemWithSummary(BaseSchemaModel):
    """Enhanced interview item that includes summary report data for completed interviews."""

    interview_id: int = pydantic.Field(
        description="Unique identifier for the interview"
    )
    track: str
    difficulty: str
    status: str
    created_at: datetime.datetime
    knowledge_percentage: float | None = pydantic.Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Knowledge competence percentage from summary report",
    )
    speech_fluency_percentage: float | None = pydantic.Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Speech fluency percentage from summary report",
    )
    summary_report_available: bool = pydantic.Field(
        default=False, description="Whether a summary report exists for this interview"
    )
    attempts_count: int = pydantic.Field(
        default=0,
        ge=0,
        description="Number of summary reports/attempts for this interview",
    )
    top_action_items: list[str] = pydantic.Field(
        default_factory=list,
        description="Top 3 action items from the latest summary report",
    )


class InterviewsListResponse(BaseSchemaModel):
    items: list[InterviewItem]
    next_cursor: int | None
    limit: int


class InterviewsListWithSummaryResponse(BaseSchemaModel):
    """Enhanced interviews list response that includes summary report data."""

    items: list[InterviewItemWithSummary]
    next_cursor: int | None
    limit: int


class QuestionAttemptItem(BaseSchemaModel):
    question_attempt_id: int = pydantic.Field(
        description="Unique identifier for the question attempt"
    )
    question_text: str
    question_id: int | None = None
    audio_url: str | None = None
    transcription: dict | None = None
    created_at: datetime.datetime


class QuestionsListResponse(BaseSchemaModel):
    interview_id: int
    items: list[InterviewQuestionOut]
    next_cursor: int | None
    limit: int


class QuestionAttemptsListResponse(BaseSchemaModel):
    interview_id: int
    items: list[QuestionAttemptItem]
    next_cursor: int | None
    limit: int


class CompleteInterviewRequest(BaseSchemaModel):
    interview_id: int
    session_lease_token: str | None = pydantic.Field(
        default=None, min_length=20, max_length=256
    )


AdvanceInterviewAction = Literal["answer_saved", "skip", "continue_without_follow_up"]


class AdvanceInterviewRequest(BaseSchemaModel):
    interview_id: int
    from_question_id: int
    expected_state_version: int = pydantic.Field(ge=0)
    transition_id: str = pydantic.Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="Stable idempotency key retained across client retries",
    )
    action: AdvanceInterviewAction
    answer_version: int | None = pydantic.Field(default=None, ge=0)
    session_lease_token: str | None = pydantic.Field(
        default=None, min_length=20, max_length=256
    )


class AdvanceInterviewResponse(BaseSchemaModel):
    interview_id: int
    status: str
    state_version: int = pydantic.Field(ge=0)
    question_set_version: int = pydantic.Field(ge=0)
    current_question_id: int | None = None
    current_question_index: int | None = None
    complete_ready: bool
    duplicate: bool = False
    questions: list[ResumeQuestionOut]


class PauseInterviewRequest(BaseSchemaModel):
    interview_id: int
    session_lease_token: str | None = pydantic.Field(
        default=None, min_length=20, max_length=256
    )


class PauseInterviewResponse(BaseSchemaModel):
    interview_id: int
    status: Literal["paused"]
    paused_at: datetime.datetime | None = None
    expires_at: datetime.datetime | None = None


class ClaimSessionLeaseRequest(BaseSchemaModel):
    interview_id: int
    lease_token: str | None = pydantic.Field(
        default=None, min_length=20, max_length=256
    )
    force: bool = False


class HeartbeatSessionLeaseRequest(BaseSchemaModel):
    interview_id: int
    lease_token: str = pydantic.Field(min_length=20, max_length=256)


class SessionLeaseResponse(BaseSchemaModel):
    interview_id: int
    lease_token: str
    lease_expires_at: datetime.datetime
    state: Literal["acquired", "renewed", "taken_over"]


#: What the client is allowed to report. Anything else is a 422, not a free-text
#: reason field - the facilitator-visible remark reads from a fixed, known set.
ProctoringViolationReason = Literal["tab_switch", "fullscreen_exit"]


class ReportProctoringViolationRequest(BaseSchemaModel):
    interview_id: int
    reason: ProctoringViolationReason


class ProctoringViolationResponse(BaseSchemaModel):
    interview_id: int
    status: str
    proctoring_terminated: bool
    proctoring_violation_reason: str | None = None


class ResumeInterviewRequest(BaseSchemaModel):
    interview_id: int = pydantic.Field(description="ID of the interview to resume")


class ResumeInterviewResponse(BaseSchemaModel):
    interview_id: int = pydantic.Field(description="ID of the interview")
    track: str = pydantic.Field(description="Interview track")
    difficulty: str = pydantic.Field(description="Interview difficulty")
    questions: list[ResumeQuestionOut] = pydantic.Field(
        description="The complete immutable question snapshot with durable answer checkpoints"
    )
    total_questions: int = pydantic.Field(
        description="Total number of questions in the interview"
    )
    attempted_questions: int = pydantic.Field(
        description="Number of questions with attempts"
    )
    answered_questions: int = pydantic.Field(
        description="Number of questions whose answer is durably saved"
    )
    remaining_questions: int = pydantic.Field(
        description="Number of questions without a durably saved answer"
    )
    resume_question_index: int | None = pydantic.Field(
        description="Zero-based authoritative current question, or null after terminal navigation"
    )
    state_version: int = pydantic.Field(default=0, ge=0)
    question_set_version: int = pydantic.Field(default=0, ge=0)
    current_question_id: int | None = None
    current_question_index: int | None = None
    complete_ready: bool = False
    status: str = pydantic.Field(description="Authoritative interview lifecycle status")
    paused_at: datetime.datetime | None = None
    expires_at: datetime.datetime | None = None
    interview_mode: str = pydantic.Field(
        default="practice",
        description=(
            "Snapshotted on the interview at creation. A resume can't rely on the "
            "URL the client happened to arrive with, so this is how the client "
            "learns whether to activate proctoring when continuing a sitting."
        ),
    )


class QuestionSupplementsResponse(BaseSchemaModel):
    interview_id: int = pydantic.Field(description="Interview identifier")
    supplements: list[QuestionSupplementOut] = pydantic.Field(
        description="Generated supplements mapped to questions"
    )
