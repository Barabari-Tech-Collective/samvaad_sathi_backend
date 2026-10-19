import asyncio
import json
import logging

import fastapi
from src.services.interview_question_plan import planned_count, snapshotted_questions
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.repository import get_repository
from src.api.dependencies.session import get_async_session
from src.models.schemas.interview import (
    AdvanceInterviewRequest,
    AdvanceInterviewResponse,
    ClaimSessionLeaseRequest,
    CompleteInterviewRequest,
    CompletePracticeDrillRequest,
    CompletePracticeDrillResponse,
    CreateAttemptRequest,
    CreateAttemptResponse,
    GeneratedQuestionsInResponse,
    GenerateQuestionsRequest,
    HeartbeatSessionLeaseRequest,
    InterviewCreate,
    InterviewInResponse,
    InterviewItem,
    InterviewItemWithSummary,
    InterviewQuestionOut,
    InterviewsListResponse,
    InterviewsListWithSummaryResponse,
    PauseInterviewRequest,
    PauseInterviewResponse,
    PracticeHubResponse,
    ProctoringViolationResponse,
    QuestionAttemptItem,
    QuestionAttemptsListResponse,
    QuestionsListResponse,
    ReportProctoringViolationRequest,
    ResumeInterviewRequest,
    ResumeInterviewResponse,
    ResumeQuestionOut,
    SessionLeaseResponse,
)
from src.repository.crud.interview import InterviewCRUDRepository
from src.repository.crud.interview_question import InterviewQuestionCRUDRepository
from src.repository.crud.question import (
    AttemptIdempotencyConflict,
    QuestionAttemptCRUDRepository,
)
from src.services.analytics_events import track_analytics_event
from src.services.interview_lifecycle import interview_has_expired
from src.services.interview_session_lease import (
    session_lease_is_active,
    session_lease_matches,
)
from src.services.llm import generate_interview_questions_with_llm
from src.services.practice_drill_completion import record_practice_drill_completion
from src.services.practice_hub import build_practice_hub
from src.services.practice_feature_controls import require_practice_feature
from src.services.practice_navigation import (
    NavigationConflict,
    apply_navigation_transition,
    has_durable_answer,
    initialize_navigation,
    question_snapshot_hash,
)
from src.services.question_snapshot import (
    lock_question_generation,
    question_items_from_persisted,
)
from src.services.question_supplements import (
    QuestionSupplementService,
    serialize_question_supplement,
)
from src.services.static_questions import get_static_questions
from src.services.syllabus_service import syllabus_service
from src.services.whisper import strip_word_level_data

logger = logging.getLogger(__name__)


router = fastapi.APIRouter(prefix="", tags=["interviews"])


@router.get(
    path="/practice/hub",
    name="practice:hub",
    response_model=PracticeHubResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Get the current student's evidence-safe Practice coach plan",
)
async def get_practice_hub(
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
) -> PracticeHubResponse:
    return await build_practice_hub(
        interview_repo.async_session,
        user_id=current_user.id,
    )


@router.post(
    path="/practice/drills/complete",
    name="practice:complete-drill",
    response_model=CompletePracticeDrillResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Record a Practice skill-builder completion idempotently",
)
async def complete_practice_drill(
    payload: CompletePracticeDrillRequest,
    current_user=fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> CompletePracticeDrillResponse:
    completion, created = await record_practice_drill_completion(
        session,
        user_id=current_user.id,
        payload=payload,
    )
    return CompletePracticeDrillResponse(
        completion_id=completion.id,
        created=created,
        completed_at=completion.completed_at,
    )


def _session_lease_error(code: str, message: str) -> fastapi.HTTPException:
    return fastapi.HTTPException(
        status_code=fastapi.status.HTTP_409_CONFLICT,
        detail={"code": code, "message": message},
    )


def _navigation_error(
    code: str,
    message: str,
    *,
    interview=None,
) -> fastapi.HTTPException:
    detail = {
        "code": code,
        "message": message,
        "stateVersion": int(getattr(interview, "state_version", 0) or 0),
        "currentQuestionId": getattr(interview, "current_question_id", None),
    }
    return fastapi.HTTPException(
        status_code=fastapi.status.HTTP_409_CONFLICT,
        detail=detail,
    )


def _require_session_lease(interview, lease_token: str | None) -> None:
    if getattr(interview, "interview_mode", None) != "practice" or not getattr(
        interview, "session_lease_required", False
    ):
        return
    if not session_lease_matches(interview, lease_token):
        raise _session_lease_error(
            "SESSION_LEASE_REPLACED",
            "This interview is active on another device or tab.",
        )
    if not session_lease_is_active(interview):
        raise _session_lease_error(
            "SESSION_LEASE_EXPIRED",
            "Your connection lock expired. Reconnect before saving this answer.",
        )


async def _lock_owned_interview(interview_repo, *, interview_id: int, user_id: int):
    lock = getattr(interview_repo, "lock_by_id_and_user", None)
    if callable(lock):
        return await lock(interview_id=interview_id, user_id=user_id)
    get_owned = getattr(interview_repo, "get_by_id_and_user", None)
    if callable(get_owned):
        return await get_owned(interview_id, user_id)
    interview = await interview_repo.get_by_id(interview_id=interview_id)
    if interview is None or interview.user_id != user_id:
        return None
    return interview


async def _list_attempts_for_update(attempt_repo, *, interview_id: int) -> list:
    locked_list = getattr(attempt_repo, "list_by_interview_for_update", None)
    if callable(locked_list):
        return await locked_list(interview_id=interview_id)
    return await attempt_repo.list_by_interview(interview_id=interview_id)


async def _commit_navigation_state(interview_repo, interview) -> None:
    session = getattr(interview_repo, "async_session", None)
    commit = getattr(session, "commit", None)
    if callable(commit):
        await commit()
    refresh = getattr(session, "refresh", None)
    if callable(refresh):
        await refresh(interview)


def _supplements_enabled_for_track(track: str | None) -> bool:
    normalized_track = (track or "").strip().lower()
    return not normalized_track.startswith("non-tech:")


def _attempts_by_question_id(attempts: list) -> dict[int, object]:
    """Return the latest attempt per question from an ID-ordered collection."""
    return {
        int(attempt.question_id): attempt
        for attempt in attempts
        if attempt.question_id is not None
    }


def _assert_question_snapshot(interview, questions: list) -> None:
    expected_hash = getattr(interview, "question_set_hash", None)
    if not expected_hash:
        return
    actual_hash = question_snapshot_hash(questions)
    if actual_hash != expected_hash:
        raise _navigation_error(
            "QUESTION_SNAPSHOT_CHANGED",
            "The saved question set failed its integrity check. This interview was stopped so no answers are attached to changed questions.",
            interview=interview,
        )


async def _serialize_resume_questions(
    *,
    interview,
    questions: list,
    attempts_by_question_id: dict[int, object],
    question_repo: InterviewQuestionCRUDRepository,
) -> tuple[list[ResumeQuestionOut], int | None]:
    supplement_map = await _get_supplement_map(
        interview_id=interview.id,
        async_session=question_repo.async_session,
        ensure_generate=True,
        track=interview.track,
    )
    items: list[ResumeQuestionOut] = []
    current_index: int | None = None
    for index, question in enumerate(questions):
        attempt = attempts_by_question_id.get(question.id)
        skipped = bool(
            question.status == "skipped"
            or getattr(attempt, "attempt_state", None) == "skipped"
        )
        if question.id == getattr(interview, "current_question_id", None):
            current_index = index
        items.append(
            ResumeQuestionOut(
                interview_question_id=question.id,
                text=question.text,
                topic=question.topic,
                category=question.category,
                status=question.status,
                resume_used=question.resume_used,
                is_follow_up=question.is_follow_up,
                parent_question_id=question.parent_question_id,
                follow_up_strategy=question.follow_up_strategy,
                supplement=supplement_map.get(question.id),
                question_attempt_id=attempt.id if attempt else None,
                answer_saved=has_durable_answer(attempt),
                answer_version=(attempt.answer_version or 0) if attempt else 0,
                answer_kind=attempt.answer_kind if attempt else None,
                transcription_status=(
                    attempt.transcription_status if attempt else None
                ),
                attempt_state=getattr(attempt, "attempt_state", None),
                skipped=skipped,
                follow_up_disposition=getattr(attempt, "follow_up_disposition", None),
            )
        )
    return items, current_index


@router.post(
    path="/interviews/create",
    name="interviews:create",
    response_model=InterviewInResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Create or resume an interview session",
    description=(
        "Starts a new interview session for the current user with the specified track, or resumes the active session "
        "if one already exists for that track. Accepts optional 'difficulty' (easy|medium|hard|expert), default 'medium'."
    ),
)
async def create_or_resume_interview(
    payload: InterviewCreate,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
) -> InterviewInResponse:
    if payload.course_id is not None and payload.course_id not in (
        getattr(current_user, "enrolled_course_ids", None) or []
    ):
        raise fastapi.HTTPException(
            403, "Current enrollment in this course is required."
        )
    if (
        payload.question_count is not None
        and payload.client_contract != "fixed-round-v1"
    ):
        raise fastapi.HTTPException(
            409, "Refresh to the current app before starting a fixed-length round."
        )
    if payload.job_profile_id is not None:
        raise fastapi.HTTPException(
            409, "Use the V2 interview entry point for a creator's published role."
        )
    # Check if the user has an active session; if so, resume instead of creating a new one
    active = await interview_repo.get_resumable_by_user(user_id=current_user.id)
    if (
        active is not None
        and active.track == payload.track
        and getattr(active, "course_id", None) == payload.course_id
        and active.job_profile_id is None
        and (
            payload.question_count is None
            or planned_count(active) == payload.question_count
        )
    ):
        return InterviewInResponse(
            interview_id=active.id,
            track=active.track,
            difficulty=active.difficulty,
            status=active.status,
            created_at=active.created_at,
            resumed=True,
            interview_mode=active.interview_mode,
            planned_question_count=planned_count(active),
            paused_at=getattr(active, "paused_at", None),
            expires_at=getattr(active, "expires_at", None),
        )

    await require_practice_feature(interview_repo.async_session, "practice_new_starts")

    difficulty = (payload.difficulty or "medium").lower()
    if difficulty not in ("easy", "medium", "hard", "expert"):
        difficulty = "medium"
    interview = await interview_repo.create_interview(
        user_id=current_user.id,
        track=payload.track,
        difficulty=difficulty,
        course_id=payload.course_id,
        question_count=payload.question_count,
    )
    await track_analytics_event(
        interview_repo.async_session,
        event_type="interview_started",
        user_id=current_user.id,
        interview_id=interview.id,
        event_data={
            "track": interview.track,
            "difficulty": interview.difficulty,
            "api": "interviews.create",
        },
    )
    await track_analytics_event(
        interview_repo.async_session,
        event_type="role_selected",
        user_id=current_user.id,
        interview_id=interview.id,
        event_data={"track": interview.track, "source": "interviews.create"},
    )
    res = InterviewInResponse(
        interview_id=interview.id,
        track=interview.track,
        difficulty=interview.difficulty,
        status=interview.status,
        created_at=interview.created_at,
        resumed=False,
        interview_mode=interview.interview_mode,
        planned_question_count=planned_count(interview),
        paused_at=getattr(interview, "paused_at", None),
        expires_at=getattr(interview, "expires_at", None),
    )

    logger.info(
        "POST /interviews/create NEW RESPONSE | User=%s | Interview=%s | Response=\n%s",
        current_user.id,
        interview.id,
        json.dumps(res.model_dump(), indent=2, default=str),
    )

    return res


@router.post(
    path="/interviews/session/claim",
    name="interviews:claim-session-lease",
    response_model=SessionLeaseResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Claim the active Practice interview session",
)
async def claim_session_lease(
    payload: ClaimSessionLeaseRequest,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
) -> SessionLeaseResponse:
    interview, state, token = await interview_repo.claim_session_lease(
        interview_id=payload.interview_id,
        user_id=current_user.id,
        presented_token=payload.lease_token,
        force=payload.force,
    )
    if interview is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Interview not found",
        )
    if state == "not_practice":
        raise _session_lease_error(
            "SESSION_LEASE_NOT_AVAILABLE",
            "Session takeover is available only for Practice interviews.",
        )
    if state == "expired":
        await interview_repo.mark_expired(interview_id=interview.id)
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_410_GONE,
            detail="This Practice interview has expired. Start a new interview.",
        )
    if interview.status != "active":
        raise _session_lease_error(
            "SESSION_NOT_ACTIVE",
            "This interview is not active.",
        )
    if state == "conflict":
        raise _session_lease_error(
            "SESSION_ACTIVE_ELSEWHERE",
            "This interview is already open on another device or tab.",
        )
    if token is None or interview.session_lease_expires_at is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The interview session could not be secured. Try again.",
        )
    return SessionLeaseResponse(
        interview_id=interview.id,
        lease_token=token,
        lease_expires_at=interview.session_lease_expires_at,
        state=state,
    )


@router.post(
    path="/interviews/session/heartbeat",
    name="interviews:heartbeat-session-lease",
    response_model=SessionLeaseResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Keep the active Practice interview session alive",
)
async def heartbeat_session_lease(
    payload: HeartbeatSessionLeaseRequest,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
) -> SessionLeaseResponse:
    interview = await interview_repo.heartbeat_session_lease(
        interview_id=payload.interview_id,
        user_id=current_user.id,
        lease_token=payload.lease_token,
    )
    if interview is None:
        raise _session_lease_error(
            "SESSION_LEASE_REPLACED",
            "This interview is active on another device or is no longer available.",
        )
    return SessionLeaseResponse(
        interview_id=interview.id,
        lease_token=payload.lease_token,
        lease_expires_at=interview.session_lease_expires_at,
        state="renewed",
    )


@router.post(
    path="/interviews/pause",
    name="interviews:pause",
    response_model=PauseInterviewResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Save and exit a Practice interview",
)
async def pause_interview(
    payload: PauseInterviewRequest,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
) -> PauseInterviewResponse:
    interview = await _lock_owned_interview(
        interview_repo, interview_id=payload.interview_id, user_id=current_user.id
    )
    if interview is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Interview not found",
        )
    if interview.interview_mode != "practice":
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT,
            detail="Save and exit is available only for Practice interviews.",
        )
    if interview.status == "expired" or (
        interview.status in ("active", "paused") and interview_has_expired(interview)
    ):
        await interview_repo.mark_expired(interview_id=interview.id)
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_410_GONE,
            detail="This Practice interview has expired. Start a new interview.",
        )
    if interview.status == "paused":
        paused = interview
    elif interview.status == "active":
        _require_session_lease(interview, payload.session_lease_token)
        paused = await interview_repo.mark_paused(interview_id=interview.id)
        if paused is None:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_409_CONFLICT,
                detail="The interview changed while it was being paused. Try again.",
            )
        await track_analytics_event(
            interview_repo.async_session,
            event_type="interview_paused",
            user_id=current_user.id,
            interview_id=paused.id,
            event_data={"api": "interviews.pause"},
        )
    else:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT,
            detail="Only an active Practice interview can be paused.",
        )
    return PauseInterviewResponse(
        interview_id=paused.id,
        status="paused",
        paused_at=paused.paused_at,
        expires_at=paused.expires_at,
    )


@router.post(
    path="/interviews/generate-questions",
    name="interviews:generate-questions",
    response_model=GeneratedQuestionsInResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Generate interview questions for an interview",
    description=(
        "Generates and persists a set of questions for an interview belonging to the current user. By default targets the "
        "active interview; you may also specify an explicit interviewId in the request body. Uses an LLM when available, "
        "falls back to static questions otherwise. Accepts optional 'use_resume' boolean (default true) to control whether "
        "resume text is used for question generation."
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "default": {
                            "summary": "Use active interview with resume",
                            "value": {"useResume": True},
                        },
                        "specificInterview": {
                            "summary": "Target specific interview",
                            "value": {"interviewId": 123, "useResume": True},
                        },
                    }
                }
            }
        }
    },
)
async def generate_questions(
    payload: GenerateQuestionsRequest = fastapi.Body(
        ...,  # required body
        examples={
            "default": {
                "summary": "Use active interview with resume",
                "value": {"useResume": True},
            },
            "specificInterview": {
                "summary": "Target specific interview",
                "value": {"interviewId": 123, "useResume": True},
            },
        },
    ),
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
    question_repo: InterviewQuestionCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewQuestionCRUDRepository)
    ),
) -> GeneratedQuestionsInResponse:
    await require_practice_feature(question_repo.async_session, "question_generation")
    # Determine target interview: explicit interview_id (if provided and belongs to user) else current active
    interview = None
    if getattr(payload, "interview_id", None) is not None:
        interview = await interview_repo.get_by_id(interview_id=payload.interview_id)  # type: ignore[arg-type]
        if interview is None or interview.user_id != current_user.id:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_404_NOT_FOUND,
                detail="Interview not found",
            )
        if interview.status != "active":
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_400_BAD_REQUEST,
                detail="Only active interviews can generate questions",
            )
    else:
        interview = await interview_repo.get_active_by_user(user_id=current_user.id)
        if interview is None:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_400_BAD_REQUEST,
                detail="No active interview to generate questions for",
            )

    # This endpoint has no job-profile awareness: it goes straight to the
    # syllabus and the LLM. Serving a curated interview from here would assess a
    # student against a facilitator's role on questions the facilitator never
    # wrote - and because question sets are immutable once persisted, whichever
    # generator ran first would decide that permanently. The student app only
    # calls the v2 generator; this guards the endpoint against other callers.
    if getattr(interview, "job_profile_id", None):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT,
            detail=(
                "This interview is based on a facilitator's role and must be generated "
                "through /v2/interviews/generate-questions, which serves their published questions."
            ),
        )

    # Serialize against the other generators (interviews_v2) before reading, or
    # two requests both see no questions and both insert a full set.
    await lock_question_generation(
        question_repo.async_session, interview_id=interview.id
    )

    # Check if questions already exist for this interview (idempotent)
    existing = await question_repo.list_by_interview(interview_id=interview.id)
    persisted = existing
    cached = bool(existing)

    if not existing:
        # Generate questions only if they don't exist
        # Use resume context if present on user and use_resume is True
        resume_context = (
            getattr(current_user, "resume_text", None) if payload.use_resume else None
        )
        # Build influence knobs
        years = getattr(current_user, "years_experience", None)
        skills_json = getattr(current_user, "skills", None) or {}
        skills_list = list(
            skills_json.get("items", []) if isinstance(skills_json, dict) else []
        )
        has_resume = bool(resume_context)
        has_skills = bool(skills_list)

        role = syllabus_service._role_manager.derive_role(interview.track)
        topic_bank = syllabus_service.get_topics_for_role(
            role=role, difficulty=interview.difficulty
        )

        # Convert TopicBank to dict format for backward compatibility
        topics = {
            "tech": topic_bank.tech,
            "tech_allied": topic_bank.tech_allied,
            "behavioral": topic_bank.behavioral,
            "archetypes": topic_bank.archetypes,
            "depth_guidelines": topic_bank.depth_guidelines,
        }

        # Prefer tech_allied topics derived from resume/skills when available
        topics["tech_allied"] = syllabus_service.extract_tech_allied_from_resume(
            resume_text=resume_context if isinstance(resume_context, str) else None,
            skills=[str(s) for s in skills_list],
            fallback_topics=topics.get("tech_allied", []),
        )
        question_ratio = syllabus_service.compute_question_ratio(
            years_experience=years, has_resume_text=has_resume, has_skills=has_skills
        )

        # Convert QuestionRatio to dict format for backward compatibility
        ratio = {
            "tech": question_ratio.tech,
            "tech_allied": question_ratio.tech_allied,
            "behavioral": question_ratio.behavioral,
        }

        influence = {
            "target_role": role,  # Tech influence
            "difficulty": interview.difficulty,  # Tech influence
            "experience_years": years,  # Tech-allied influence
            "skills": skills_list,  # Tech influence
            "headline": getattr(
                current_user, "target_position", None
            ),  # Tech-allied influence
        }

        # For easy difficulty, use static pre-defined questions instead of LLM generation
        if interview.difficulty == "easy":
            static_items = get_static_questions(
                role=role, count=planned_count(interview) or 5, ratio=ratio
            )
            questions = [item["text"] for item in static_items]
            llm_error = None
            latency_ms = 0
            llm_model = "static"
            items = static_items
        else:
            # For medium, hard, expert: use LLM generation
            questions, llm_error, latency_ms, llm_model, items = (
                await generate_interview_questions_with_llm(
                    track=interview.track,
                    context_text=resume_context,
                    count=planned_count(interview) or 5,
                    difficulty=interview.difficulty,
                    syllabus_topics=topics,
                    ratio=ratio,
                    influence=influence,
                )
            )

        if not questions:
            questions = [
                f"Describe your recent project in {interview.track}.",
                f"What core concepts are essential in {interview.track}?",
                f"Explain a challenging problem you solved in {interview.track} and how.",
                f"How do you evaluate models in {interview.track}?",
                f"Discuss trade-offs between common methods in {interview.track}.",
            ]

        # Convert questions and items to the format expected by create_batch
        questions_data = []
        if items:  # If we have structured data from LLM
            for item in items:
                questions_data.append(
                    {
                        "text": item.get("text", ""),
                        "topic": item.get("topic"),
                        "category": item.get("category"),
                    }
                )
        else:  # Fallback to plain question strings
            for question in questions:
                questions_data.append(
                    {"text": question, "topic": None, "category": None}
                )

        persisted = await question_repo.create_batch(
            interview_id=interview.id,
            questions_data=questions_data,
            resume_used=payload.use_resume,
        )

        qs = {
            "questions": [q.text for q in persisted],
            "llm_error": llm_error,
            "latency_ms": latency_ms,
            "llm_model": llm_model,
            "items": question_items_from_persisted(
                persisted, difficulty=interview.difficulty
            ),
        }
    else:
        # Questions already exist, prepare response data from existing questions
        qs = {
            "questions": [q.text for q in existing],
            "llm_error": None,
            "latency_ms": None,
            "llm_model": None,
            "items": question_items_from_persisted(
                existing, difficulty=interview.difficulty
            ),
        }

    response = GeneratedQuestionsInResponse(
        interview_id=interview.id,
        track=interview.track,
        count=len(persisted),
        questions=[q.text for q in persisted],
        question_ids=[q.id for q in persisted],  # Include question IDs for consistency
        items=qs.get("items"),
        cached=cached,
        llm_model=qs.get("llm_model"),  # type: ignore[union-attr]
        llm_latency_ms=qs.get("latency_ms"),  # type: ignore[union-attr]
        llm_error=qs.get("llm_error"),  # type: ignore[union-attr]
        interview_mode=interview.interview_mode,
        planned_question_count=planned_count(interview),
    )
    logger.info(
        "POST /interviews/generate-questions FULL RESPONSE PAYLOAD | Interview=%s | Cached=%s | Response=\n%s",
        interview.id,
        cached,
        json.dumps(response.model_dump(), indent=2, default=str),
    )
    return response


@router.post(
    path="/interviews/navigation/advance",
    name="interviews:advance-navigation",
    response_model=AdvanceInterviewResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Atomically advance a Practice interview checkpoint",
    description=(
        "Uses optimistic concurrency and an idempotency key so retries, double taps, "
        "refreshes, and competing tabs cannot skip or reorder questions."
    ),
)
async def advance_interview_navigation(
    payload: AdvanceInterviewRequest,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
    question_repo: InterviewQuestionCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewQuestionCRUDRepository)
    ),
    attempt_repo: QuestionAttemptCRUDRepository = fastapi.Depends(
        get_repository(repo_type=QuestionAttemptCRUDRepository)
    ),
) -> AdvanceInterviewResponse:
    interview = await _lock_owned_interview(
        interview_repo,
        interview_id=payload.interview_id,
        user_id=current_user.id,
    )
    if interview is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Interview not found",
        )
    if interview.interview_mode != "practice" and planned_count(interview) is None:
        raise _navigation_error(
            "PRACTICE_NAVIGATION_REQUIRED",
            "This navigation contract is available only for Practice interviews.",
            interview=interview,
        )
    if interview.status == "expired" or interview_has_expired(interview):
        await interview_repo.mark_expired(interview_id=interview.id)
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_410_GONE,
            detail="This Practice interview has expired. Start a new interview.",
        )
    if interview.status != "active":
        raise _navigation_error(
            "INTERVIEW_NOT_ACTIVE",
            "Resume this Practice interview before continuing.",
            interview=interview,
        )
    _require_session_lease(interview, payload.session_lease_token)

    # The background worker locks the parent attempt before publishing an
    # adaptive child. Acquire the same lock first, then read questions, so the
    # child is either fully visible or the student's explicit skip wins.
    # Locking questions first would invert the worker's order and can deadlock.
    attempts = await _list_attempts_for_update(attempt_repo, interview_id=interview.id)
    questions = await question_repo.list_by_interview(interview_id=interview.id)
    attempt_map = _attempts_by_question_id(attempts)
    initialize_navigation(interview, questions, attempt_map)
    _assert_question_snapshot(interview, questions)

    try:
        result = apply_navigation_transition(
            interview=interview,
            questions=questions,
            attempts_by_question_id=attempt_map,
            from_question_id=payload.from_question_id,
            expected_state_version=payload.expected_state_version,
            transition_id=payload.transition_id,
            action=payload.action,
            answer_version=payload.answer_version,
        )
    except NavigationConflict as exc:
        # Build the response before rollback. SQLAlchemy expires ORM state on
        # rollback, so reading interview fields afterwards can trigger an
        # asynchronous refresh from synchronous exception-serialization code.
        conflict_response = _navigation_error(
            exc.code, exc.message, interview=interview
        )
        await interview_repo.async_session.rollback()
        raise conflict_response from exc

    await _commit_navigation_state(interview_repo, interview)
    # A concurrent worker can only append an adaptive child while holding the
    # parent attempt lock used above. Reload after commit so the response is the
    # same canonical queue a refresh would receive.
    questions = await question_repo.list_by_interview(interview_id=interview.id)
    attempts = await attempt_repo.list_by_interview(interview_id=interview.id)
    attempt_map = _attempts_by_question_id(attempts)
    question_items, current_index = await _serialize_resume_questions(
        interview=interview,
        questions=questions,
        attempts_by_question_id=attempt_map,
        question_repo=question_repo,
    )
    return AdvanceInterviewResponse(
        planned_question_count=planned_count(interview),
        interview_id=interview.id,
        status=interview.status,
        state_version=result.state_version,
        question_set_version=int(interview.question_set_version or 0),
        current_question_id=result.current_question_id,
        current_question_index=current_index,
        complete_ready=result.complete_ready,
        duplicate=result.duplicate,
        questions=question_items,
    )


@router.post("/interviews/begin")
async def begin_fixed_interview(
    payload: ResumeInterviewRequest,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
    question_repo: InterviewQuestionCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewQuestionCRUDRepository)
    ),
):
    from src.services.fixed_interview_lifecycle import begin

    interview = await _lock_owned_interview(
        interview_repo, interview_id=payload.interview_id, user_id=current_user.id
    )
    if interview is None:
        raise fastapi.HTTPException(404, "Interview not found")
    questions = await question_repo.list_by_interview(interview_id=interview.id)
    result = begin(interview, questions)
    await interview_repo.async_session.commit()
    return result


@router.get("/interviews/{interview_id}/receipt")
async def get_submission_receipt(
    interview_id: int,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
):
    interview = await interview_repo.get_by_id(interview_id=interview_id)
    if interview is None or interview.user_id != current_user.id:
        raise fastapi.HTTPException(404, "Interview not found")
    return {
        "receipt": getattr(interview, "submission_receipt", None),
        "status": interview.status,
        "interviewMode": interview.interview_mode,
    }


@router.post(
    path="/interviews/complete",
    name="interviews:complete",
    status_code=fastapi.status.HTTP_200_OK,
    summary="Complete an interview session by id",
    description="Marks the specified interview as completed if it belongs to the current user.",
)
async def complete_interview(
    payload: CompleteInterviewRequest,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
):
    interview = await _lock_owned_interview(
        interview_repo, interview_id=payload.interview_id, user_id=current_user.id
    )
    if interview is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Interview not found"
        )
    if interview.status == "completed":
        return {
            "id": interview.id,
            "status": interview.status,
            "message": "Interview already completed",
            "receipt": getattr(interview, "submission_receipt", None),
        }
    _require_session_lease(interview, payload.session_lease_token)
    if interview.status == "expired" or (
        interview.status in ("active", "paused") and interview_has_expired(interview)
    ):
        await interview_repo.mark_expired(interview_id=interview.id)
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_410_GONE,
            detail="This Practice interview has expired. Start a new interview.",
        )
    if interview.status != "active":
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail="Only active interviews can be completed",
        )
    deadline = getattr(interview, "answer_deadline_at", None)
    from src.services.fixed_interview_lifecycle import utcnow

    deadline_passed = deadline is not None and utcnow() > deadline
    if planned_count(interview) is not None:
        from src.services.fixed_interview_lifecycle import receipt
        from src.repository.crud.interview_question import (
            InterviewQuestionCRUDRepository,
        )
        from src.repository.crud.question import QuestionAttemptCRUDRepository

        session = interview_repo.async_session
        questions = await InterviewQuestionCRUDRepository(session).list_by_interview(
            interview_id=interview.id
        )
        attempts = await QuestionAttemptCRUDRepository(session).list_by_interview(
            interview_id=interview.id
        )
        interview.submission_receipt = receipt(interview, questions, attempts)
    if (
        not deadline_passed
        and (
            interview.interview_mode == "practice"
            or planned_count(interview) is not None
        )
        and int(getattr(interview, "question_set_version", 0) or 0) > 0
        and getattr(interview, "current_question_id", None) is not None
    ):
        raise _navigation_error(
            "INTERVIEW_NOT_READY_TO_COMPLETE",
            "Finish or explicitly skip the current question before completing this interview.",
            interview=interview,
        )
    updated = await interview_repo.mark_completed(interview_id=interview.id)
    if updated is not None:
        await track_analytics_event(
            interview_repo.async_session,
            event_type="interview_completed",
            user_id=current_user.id,
            interview_id=updated.id,
            event_data={
                "track": updated.track,
                "difficulty": updated.difficulty,
                "duration_seconds": updated.duration_seconds,
                "api": "interviews.complete",
            },
        )
    return {
        "id": updated.id if updated else None,
        "status": updated.status if updated else None,
        "receipt": getattr(updated, "submission_receipt", None) if updated else None,
        "message": (
            "Interview marked as completed" if updated else "No update performed"
        ),
    }


@router.post(
    path="/interviews/proctoring-violation",
    name="interviews:report-proctoring-violation",
    response_model=ProctoringViolationResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Record an assessment browser signal without ending the interview",
    description="Tab/fullscreen events are review evidence, never an automatic cheating verdict. Practice is excluded.",
)
async def report_proctoring_violation(
    payload: ReportProctoringViolationRequest,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
):
    import uuid

    event_id = payload.event_id or str(uuid.uuid4())
    updated = await interview_repo.record_integrity_event(
        interview_id=payload.interview_id,
        user_id=current_user.id,
        reason=payload.reason,
        event_id=event_id,
    )
    return ProctoringViolationResponse(
        interview_id=updated.id,
        status=updated.status,
        proctoring_terminated=updated.proctoring_terminated,
        proctoring_violation_reason=updated.proctoring_violation_reason,
        event_count=len(updated.integrity_events or []),
        action=(
            "ignored_terminal"
            if updated.status != "active"
            else (
                "recorded_for_review"
                if any(e["eventId"] == event_id for e in updated.integrity_events or [])
                else "journal_full"
            )
        ),
    )


@router.get(
    path="/interviews",
    name="interviews:list",
    response_model=InterviewsListResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="List my interviews (cursor-based)",
    description=(
        "Returns the user's interviews in reverse chronological order using id-based cursor pagination. "
        "Use the returned next_cursor to fetch the next page."
    ),
)
async def list_my_interviews(
    limit: int = 20,
    cursor: int | None = None,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
) -> InterviewsListResponse:
    safe_limit = max(1, min(100, int(limit)))
    rows, next_cursor = await interview_repo.list_by_user_cursor(
        user_id=current_user.id, limit=safe_limit, cursor_id=cursor
    )

    items = []
    for interview, summary_reports, resume_used in rows:
        # Count attempts (summary reports)
        attempts_count = len(summary_reports)

        # Extract percentages from the latest report (first in the list since it's ordered by created_at desc)
        knowledge_percentage = None
        speech_fluency_percentage = None

        if summary_reports:
            latest_report = summary_reports[0]
            if latest_report.report_json:
                # Try new format first (scoreSummary)
                if "scoreSummary" in latest_report.report_json:
                    score_summary = latest_report.report_json["scoreSummary"]
                    knowledge_percentage = score_summary.get(
                        "knowledgeCompetence", {}
                    ).get("percentage")
                    speech_fluency_percentage = score_summary.get(
                        "speechAndStructure", {}
                    ).get("percentage")
                # Fall back to old format (metrics)
                elif "metrics" in latest_report.report_json:
                    metrics = latest_report.report_json.get("metrics", {})
                    knowledge_competence = metrics.get("knowledgeCompetence", {})
                    speech_structure_fluency = metrics.get("speechStructure", {})

                    knowledge_percentage = knowledge_competence.get("averagePct")
                    speech_fluency_percentage = speech_structure_fluency.get(
                        "averagePct"
                    )

        item = InterviewItem(
            interview_id=interview.id,
            track=interview.track,
            difficulty=interview.difficulty,
            status=interview.status,
            created_at=interview.created_at,
            knowledge_percentage=knowledge_percentage,
            speech_fluency_percentage=speech_fluency_percentage,
            attempts_count=attempts_count,
            resume_used=resume_used,
        )
        items.append(item)

    return InterviewsListResponse(
        items=items,
        next_cursor=next_cursor,
        limit=safe_limit,
    )


@router.get(
    path="/interviews/{interview_id}/questions",
    name="interviews:list-questions",
    response_model=QuestionsListResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="List questions for an interview (cursor-based)",
    description=(
        "Returns the questions for the given interview in ascending order using id-based cursor pagination. "
        "Use the returned next_cursor to fetch the next page."
    ),
)
async def list_interview_questions(
    interview_id: int,
    limit: int = 20,
    cursor: int | None = None,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
    question_repo: InterviewQuestionCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewQuestionCRUDRepository)
    ),
    question_attempt_repo: QuestionAttemptCRUDRepository = fastapi.Depends(
        get_repository(repo_type=QuestionAttemptCRUDRepository)
    ),
) -> QuestionsListResponse:
    interview = await interview_repo.get_by_id(interview_id=interview_id)
    if interview is None or interview.user_id != current_user.id:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Interview not found"
        )

    safe_limit = max(1, min(100, int(limit)))
    items, next_cursor = await question_repo.list_by_interview_cursor(
        interview_id=interview_id, limit=safe_limit, cursor_id=cursor
    )
    # Backfill missing parent pointers for follow-up questions using attempt metadata if needed
    await _backfill_follow_up_parents(
        questions=items,
        question_repo=question_repo,
        question_attempt_repo=question_attempt_repo,
    )

    supplement_map = await _get_supplement_map(
        interview_id=interview_id,
        async_session=question_repo.async_session,
        ensure_generate=True,
        track=interview.track,
    )

    return QuestionsListResponse(
        interview_id=interview_id,
        items=[
            InterviewQuestionOut(
                interview_question_id=q.id,
                text=q.text,
                topic=q.topic,
                category=q.category,
                status=q.status,
                resume_used=q.resume_used,
                is_follow_up=q.is_follow_up,
                parent_question_id=q.parent_question_id,
                follow_up_strategy=q.follow_up_strategy,
                supplement=supplement_map.get(q.id),
            )
            for q in items
        ],
        next_cursor=next_cursor,
        limit=safe_limit,
    )


@router.get(
    path="/interviews/{interview_id}/question-attempts",
    name="interviews:list-question-attempts",
    response_model=QuestionAttemptsListResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="List question attempts for an interview (cursor-based)",
    description=(
        "Returns the question attempts with IDs for the given interview in ascending order using id-based cursor pagination. "
        "Use this endpoint when you need QuestionAttempt IDs for audio transcription. "
        "Use the returned next_cursor to fetch the next page."
    ),
)
async def list_interview_question_attempts(
    interview_id: int,
    limit: int = 20,
    cursor: int | None = None,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
    question_repo: QuestionAttemptCRUDRepository = fastapi.Depends(
        get_repository(repo_type=QuestionAttemptCRUDRepository)
    ),
) -> QuestionAttemptsListResponse:
    interview = await interview_repo.get_by_id(interview_id=interview_id)
    if interview is None or interview.user_id != current_user.id:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Interview not found"
        )
    safe_limit = max(1, min(100, int(limit)))
    items, next_cursor = await question_repo.list_by_interview_cursor(
        interview_id=interview_id, limit=safe_limit, cursor_id=cursor
    )
    return QuestionAttemptsListResponse(
        interview_id=interview_id,
        items=[
            QuestionAttemptItem(
                question_attempt_id=q.id,
                question_text=q.question_text,
                question_id=q.question_id,
                audio_url=q.audio_url,
                transcription=strip_word_level_data(q.transcription),
                created_at=q.created_at,
            )
            for q in items
        ],
        next_cursor=next_cursor,
        limit=safe_limit,
    )


@router.post(
    path="/interviews/question-attempts",
    name="interviews:create-question-attempt",
    response_model=CreateAttemptResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Start a question attempt",
    description=(
        "Creates a new QuestionAttempt record for the specified question. "
        "Provide interviewId and questionId in the request body. "
        "Optionally updates the question status to 'in_progress'."
    ),
)
async def create_question_attempt(
    payload: CreateAttemptRequest,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
    question_repo: InterviewQuestionCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewQuestionCRUDRepository)
    ),
    attempt_repo: QuestionAttemptCRUDRepository = fastapi.Depends(
        get_repository(repo_type=QuestionAttemptCRUDRepository)
    ),
) -> CreateAttemptResponse:
    interview_id = payload.interview_id
    question_id = payload.question_id
    # Verify interview exists and belongs to user
    interview = await interview_repo.get_by_id(interview_id=interview_id)
    if interview is None or interview.user_id != current_user.id:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Interview not found"
        )

    if interview.status == "expired" or (
        interview.status in ("active", "paused") and interview_has_expired(interview)
    ):
        await interview_repo.mark_expired(interview_id=interview.id)
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_410_GONE,
            detail="This Practice interview has expired. Start a new interview.",
        )

    if interview.status != "active":
        # The gap a proctoring termination would otherwise leave open: ending
        # an interview client-side means nothing if the API still accepts new
        # answers for it. "completed" is included too - this endpoint never
        # checked status before, so a finished interview could already take
        # new attempts via a direct call.
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT,
            detail="This interview has ended and can no longer accept answers.",
        )

    # Verify question exists and belongs to the interview
    question = await question_repo.get_by_id(question_id=question_id)
    if question is None or question.interview_id != interview_id:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Question not found"
        )

    locked_interview = await _lock_owned_interview(
        interview_repo, interview_id=interview_id, user_id=current_user.id
    )
    if locked_interview is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Interview not found"
        )
    if locked_interview.status == "expired" or (
        locked_interview.status in ("active", "paused")
        and interview_has_expired(locked_interview)
    ):
        await interview_repo.mark_expired(interview_id=locked_interview.id)
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_410_GONE,
            detail="This Practice interview has expired. Start a new interview.",
        )
    if locked_interview.status != "active":
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT,
            detail="This interview has ended and can no longer accept answers.",
        )
    _require_session_lease(locked_interview, payload.session_lease_token)
    from src.services.fixed_interview_lifecycle import require_answer_window

    require_answer_window(locked_interview)
    if (
        (
            getattr(locked_interview, "interview_mode", None) == "practice"
            or planned_count(locked_interview) is not None
        )
        and int(getattr(locked_interview, "question_set_version", 0) or 0) > 0
        and locked_interview.current_question_id != question_id
    ):
        raise _navigation_error(
            "QUESTION_CHECKPOINT_CHANGED",
            "This is not the current saved question. Reload the interview checkpoint.",
            interview=locked_interview,
        )

    # Create the question attempt
    try:
        attempt, created = await attempt_repo.create_or_get_attempt(
            interview_id=interview_id,
            question_id=question_id,
            question_text=question.text,
            client_attempt_id=payload.client_attempt_id,
        )
    except AttemptIdempotencyConflict as exc:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    # Secondary display state; the durable attempt is the authoritative record.
    await question_repo.update_status(question_id=question_id, status="in_progress")

    return CreateAttemptResponse(
        question_attempt_id=attempt.id,
        created=created,
        state_version=int(getattr(locked_interview, "state_version", 0) or 0),
        current_question_id=getattr(locked_interview, "current_question_id", None),
    )


@router.get(
    path="/interviews-with-summary",
    name="interviews:list-with-summary",
    response_model=InterviewsListWithSummaryResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="List my interviews with summary report data (cursor-based)",
    description=(
        "Returns the user's interviews in reverse chronological order with summary report data for completed interviews. "
        "Includes knowledge percentage and speech fluency percentage from summary reports when available. "
        "Use the returned next_cursor to fetch the next page."
    ),
)
async def list_my_interviews_with_summary(
    limit: int = 20,
    cursor: int | None = None,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
) -> InterviewsListWithSummaryResponse:
    safe_limit = max(1, min(100, int(limit)))

    # Add timeout to prevent hanging on slow database queries
    try:
        rows, next_cursor = await asyncio.wait_for(
            interview_repo.list_by_user_cursor_with_summary(
                user_id=current_user.id, limit=safe_limit, cursor_id=cursor
            ),
            timeout=30.0,  # 30 second timeout
        )
    except asyncio.TimeoutError:
        logger.error(
            f"Timeout fetching interviews with summary for user {current_user.id}"
        )
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Request timeout while fetching interviews. Please try again.",
        )
    except Exception as e:
        logger.error(
            f"Error fetching interviews with summary for user {current_user.id}: {e}"
        )
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching interviews",
        )

    items = []
    for interview, summary_reports in rows:
        try:
            # Count attempts (summary reports)
            attempts_count = len(summary_reports) if summary_reports else 0

            # Extract percentages and action items from the latest report (first in the list since it's ordered by created_at desc)
            knowledge_percentage = None
            speech_fluency_percentage = None
            summary_report_available = attempts_count > 0
            top_action_items = []

            if summary_reports and len(summary_reports) > 0:
                try:
                    latest_report = summary_reports[0].report_json

                    # Ensure latest_report is a dict
                    if not isinstance(latest_report, dict):
                        latest_report = {}

                    # Try new format first (scoreSummary)
                    if "scoreSummary" in latest_report:
                        try:
                            score_summary = latest_report.get("scoreSummary", {})
                            if isinstance(score_summary, dict):
                                kc = score_summary.get("knowledgeCompetence", {})
                                ss = score_summary.get("speechAndStructure", {})
                                if isinstance(kc, dict):
                                    knowledge_percentage = kc.get("percentage")
                                if isinstance(ss, dict):
                                    speech_fluency_percentage = ss.get("percentage")
                        except (AttributeError, TypeError, KeyError):
                            pass
                    # Fall back to old format (metrics)
                    elif "metrics" in latest_report:
                        try:
                            metrics = latest_report.get("metrics", {})
                            if isinstance(metrics, dict):
                                kc = metrics.get("knowledgeCompetence", {}) or {}
                                ss = metrics.get("speechStructure", {}) or {}
                                if isinstance(kc, dict):
                                    knowledge_percentage = kc.get("averagePct")
                                if isinstance(ss, dict):
                                    speech_fluency_percentage = ss.get("averagePct")
                        except (AttributeError, TypeError, KeyError):
                            pass

                    # Extract action items - try new format first
                    if "overallFeedback" in latest_report:
                        try:
                            overall_feedback = latest_report.get("overallFeedback", {})
                            if isinstance(overall_feedback, dict):
                                speech_fluency = overall_feedback.get(
                                    "speechFluency", {}
                                )
                                if isinstance(speech_fluency, dict):
                                    actionable_steps = speech_fluency.get(
                                        "actionableSteps", []
                                    )
                                    if isinstance(actionable_steps, list):
                                        # New format has {title, description} objects
                                        for step in actionable_steps[:3]:
                                            if (
                                                isinstance(step, dict)
                                                and "title" in step
                                            ):
                                                title = step.get("title")
                                                if title and isinstance(title, str):
                                                    top_action_items.append(title)
                        except (AttributeError, TypeError, KeyError):
                            pass
                    # Fall back to old format
                    elif "actionableInsights" in latest_report:
                        try:
                            actionable_section = latest_report.get("actionableInsights")
                            if isinstance(actionable_section, dict):
                                groups = actionable_section.get("groups", [])
                                if isinstance(groups, list):
                                    action_items: list[str] = []
                                    for group in groups:
                                        if isinstance(group, dict):
                                            group_items = group.get("items")
                                            if isinstance(group_items, list):
                                                for item in group_items:
                                                    if item and isinstance(item, str):
                                                        action_items.append(str(item))
                                    top_action_items = action_items[:3]
                        except (AttributeError, TypeError, KeyError):
                            pass
                except (AttributeError, TypeError, IndexError, KeyError) as e:
                    # If we can't parse the report_json, log and continue with None values
                    logger.warning(
                        f"Error parsing report_json for interview {interview.id}: {e}"
                    )

            item = InterviewItemWithSummary(
                interview_id=interview.id,
                track=interview.track,
                difficulty=interview.difficulty,
                status=interview.status,
                created_at=interview.created_at,
                knowledge_percentage=knowledge_percentage,
                speech_fluency_percentage=speech_fluency_percentage,
                summary_report_available=summary_report_available,
                attempts_count=attempts_count,
                top_action_items=top_action_items,
            )
            items.append(item)
        except Exception as e:
            # If there's any error processing this interview, log it and skip it
            logger.error(
                f"Error processing interview {interview.id} in list_my_interviews_with_summary: {e}"
            )
            continue

    return InterviewsListWithSummaryResponse(
        items=items,
        next_cursor=next_cursor,
        limit=safe_limit,
    )


@router.post(
    path="/interviews/resume",
    name="interviews:resume-interview",
    response_model=ResumeInterviewResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Resume an interview from its durable server checkpoint",
    description=(
        "Returns the complete immutable question snapshot in its original order, together with "
        "the latest attempt and durable answer state for each question."
    ),
)
async def resume_interview(
    request: ResumeInterviewRequest,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewCRUDRepository)
    ),
    question_repo: InterviewQuestionCRUDRepository = fastapi.Depends(
        get_repository(repo_type=InterviewQuestionCRUDRepository)
    ),
    question_attempt_repo: QuestionAttemptCRUDRepository = fastapi.Depends(
        get_repository(repo_type=QuestionAttemptCRUDRepository)
    ),
) -> ResumeInterviewResponse:
    # Lock first: resume may initialize a legacy cursor and two reconnecting
    # clients must not choose different checkpoints.
    interview = await _lock_owned_interview(
        interview_repo,
        interview_id=request.interview_id,
        user_id=current_user.id,
    )
    if not interview:
        raise fastapi.HTTPException(
            status_code=404, detail="Interview not found or access denied"
        )
    if interview.status == "expired" or (
        interview.status in ("active", "paused") and interview_has_expired(interview)
    ):
        await interview_repo.mark_expired(interview_id=interview.id)
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_410_GONE,
            detail="This Practice interview has expired. Start a new interview.",
        )
    if interview.status == "paused":
        if interview.interview_mode != "practice":
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_409_CONFLICT,
                detail="Only Practice interviews can be resumed from a paused state.",
            )
        resumed_interview = await interview_repo.mark_resumed(interview_id=interview.id)
        if resumed_interview is None:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_409_CONFLICT,
                detail="The interview changed while it was being resumed. Try again.",
            )
        interview = resumed_interview
        await track_analytics_event(
            interview_repo.async_session,
            event_type="interview_resumed",
            user_id=current_user.id,
            interview_id=interview.id,
            event_data={"api": "interviews.resume"},
        )
        if callable(getattr(interview_repo, "lock_by_id_and_user", None)):
            interview = await _lock_owned_interview(
                interview_repo,
                interview_id=request.interview_id,
                user_id=current_user.id,
            )
            if interview is None:
                raise fastapi.HTTPException(
                    status_code=404, detail="Interview not found or access denied"
                )

    attempts = await _list_attempts_for_update(
        question_attempt_repo, interview_id=interview.id
    )
    all_questions = await question_repo.list_by_interview(interview_id=interview.id)
    total_questions = len(all_questions)
    attempt_by_question_id = _attempts_by_question_id(attempts)
    attempted_count = len(attempt_by_question_id)
    answered_count = sum(
        1 for attempt in attempt_by_question_id.values() if has_durable_answer(attempt)
    )
    initialize_navigation(interview, all_questions, attempt_by_question_id)
    _assert_question_snapshot(interview, all_questions)
    if getattr(interview, "current_question_id", None) is not None and all(
        q.id != getattr(interview, "current_question_id", None) for q in all_questions
    ):
        rollback = getattr(
            getattr(interview_repo, "async_session", None), "rollback", None
        )
        if callable(rollback):
            await rollback()
        raise _navigation_error(
            "CURRENT_QUESTION_MISSING",
            "The saved interview checkpoint no longer points to a valid question.",
            interview=interview,
        )
    await _commit_navigation_state(interview_repo, interview)

    question_items, current_question_index = await _serialize_resume_questions(
        interview=interview,
        questions=all_questions,
        attempts_by_question_id=attempt_by_question_id,
        question_repo=question_repo,
    )
    # A malicious or legacy client can leave an answer on an item later marked
    # skipped. Count each question once instead of subtracting overlapping
    # answered/skipped totals from the snapshot size.
    remaining_count = sum(
        1 for item in question_items if not item.answer_saved and not item.skipped
    )

    return ResumeInterviewResponse(
        answer_deadline_at=getattr(interview, "answer_deadline_at", None),
        started_at=getattr(interview, "started_at", None),
        interview_id=interview.id,
        track=interview.track,
        difficulty=interview.difficulty,
        questions=question_items,
        total_questions=total_questions,
        attempted_questions=attempted_count,
        answered_questions=answered_count,
        remaining_questions=remaining_count,
        resume_question_index=current_question_index,
        state_version=int(getattr(interview, "state_version", 0) or 0),
        question_set_version=int(getattr(interview, "question_set_version", 0) or 0),
        current_question_id=getattr(interview, "current_question_id", None),
        current_question_index=current_question_index,
        complete_ready=getattr(interview, "current_question_id", None) is None,
        status=interview.status,
        paused_at=getattr(interview, "paused_at", None),
        expires_at=getattr(interview, "expires_at", None),
        interview_mode=interview.interview_mode,
        planned_question_count=planned_count(interview),
    )


async def _get_supplement_map(
    *,
    interview_id: int,
    async_session,
    ensure_generate: bool = False,
    track: str | None = None,
) -> dict[int, "QuestionSupplementOut"]:
    if not _supplements_enabled_for_track(track):
        return {}
    supplement_service = QuestionSupplementService(async_session=async_session)
    try:
        if ensure_generate:
            supplements = await supplement_service.generate_for_interview(
                interview_id=interview_id,
                regenerate=False,
            )
        else:
            supplements = await supplement_service.get_for_interview(
                interview_id=interview_id
            )
    except SQLAlchemyError as exc:
        logger.warning(
            "Supplements unavailable for interview %s: %s", interview_id, exc
        )
        return {}
    return {
        supp.interview_question_id: serialize_question_supplement(supp)
        for supp in supplements
    }


async def _backfill_follow_up_parents(
    *,
    questions: list,
    question_repo: InterviewQuestionCRUDRepository,
    question_attempt_repo: QuestionAttemptCRUDRepository,
) -> None:
    """Ensure follow-up questions always have a parent_question_id."""
    for q in questions:
        if getattr(q, "is_follow_up", False) and not getattr(
            q, "parent_question_id", None
        ):
            attempt = await question_attempt_repo.get_first_by_question_id(
                question_id=q.id
            )
            parent_id = None
            if attempt and isinstance(attempt.analysis_json, dict):
                parent_id = attempt.analysis_json.get("follow_up", {}).get(
                    "parent_question_id"
                )
            if parent_id:
                await question_repo.set_parent_question(
                    question_id=q.id, parent_question_id=int(parent_id)
                )
                q.parent_question_id = int(parent_id)
