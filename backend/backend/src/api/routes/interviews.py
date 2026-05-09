import asyncio
import logging
import fastapi
from sqlalchemy.exc import SQLAlchemyError

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.repository import get_repository
from src.models.schemas.interview import InterviewCreate, InterviewInResponse, GeneratedQuestionsInResponse, InterviewsListResponse, InterviewItem, QuestionsListResponse, QuestionAttemptsListResponse, QuestionAttemptItem, GenerateQuestionsRequest, CreateAttemptResponse, InterviewQuestionOut, CompleteInterviewRequest, CreateAttemptRequest, InterviewItemWithSummary, InterviewsListWithSummaryResponse, ResumeInterviewResponse, ResumeInterviewRequest
from src.repository.crud.interview import InterviewCRUDRepository
from src.repository.crud.interview_question import InterviewQuestionCRUDRepository
from src.repository.crud.question import QuestionAttemptCRUDRepository
from src.repository.crud.question import QuestionAttemptCRUDRepository
from src.services.llm import generate_interview_questions_with_llm
from src.services.syllabus_service import syllabus_service
from src.services.whisper import strip_word_level_data
from src.services.static_questions import get_static_questions
from src.services.question_supplements import (
    QuestionSupplementService,
    serialize_question_supplement,
)
from src.services.analytics_events import track_analytics_event

logger = logging.getLogger(__name__)


router = fastapi.APIRouter(prefix="", tags=["interviews"])


def _supplements_enabled_for_track(track: str | None) -> bool:
    normalized_track = (track or "").strip().lower()
    return not normalized_track.startswith("non-tech:")


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
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
) -> InterviewInResponse:
    # Check if the user has an active session; if so, resume instead of creating a new one
    active = await interview_repo.get_active_by_user(user_id=current_user.id)
    if active is not None and active.track == payload.track:
        return InterviewInResponse(
            interview_id=active.id,
            track=active.track,
            difficulty=active.difficulty,
            status=active.status,
            created_at=active.created_at,
            resumed=True,
        )

    difficulty = (payload.difficulty or "medium").lower()
    if difficulty not in ("easy", "medium", "hard", "expert"):
        difficulty = "medium"
    interview = await interview_repo.create_interview(user_id=current_user.id, track=payload.track, difficulty=difficulty)
    await track_analytics_event(
        interview_repo.async_session,
        event_type="interview_started",
        user_id=current_user.id,
        interview_id=interview.id,
        event_data={"track": interview.track, "difficulty": interview.difficulty, "api": "interviews.create"},
    )
    await track_analytics_event(
        interview_repo.async_session,
        event_type="role_selected",
        user_id=current_user.id,
        interview_id=interview.id,
        event_data={"track": interview.track, "source": "interviews.create"},
    )
    return InterviewInResponse(
        interview_id=interview.id,
        track=interview.track,
        difficulty=interview.difficulty,
        status=interview.status,
        created_at=interview.created_at,
        resumed=False,
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
                            "value": {"useResume": True}
                        },
                        "specificInterview": {
                            "summary": "Target specific interview",
                            "value": {"interviewId": 123, "useResume": True}
                        }
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
                "value": {"useResume": True}
            },
            "specificInterview": {
                "summary": "Target specific interview",
                "value": {"interviewId": 123, "useResume": True}
            }
        }
    ),
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
    question_repo: InterviewQuestionCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewQuestionCRUDRepository)),
) -> GeneratedQuestionsInResponse:
    # Determine target interview: explicit interview_id (if provided and belongs to user) else current active
    interview = None
    if getattr(payload, "interview_id", None) is not None:
        interview = await interview_repo.get_by_id(interview_id=payload.interview_id)  # type: ignore[arg-type]
        if interview is None or interview.user_id != current_user.id:
            raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Interview not found")
        if interview.status != "active":
            raise fastapi.HTTPException(status_code=fastapi.status.HTTP_400_BAD_REQUEST, detail="Only active interviews can generate questions")
    else:
        interview = await interview_repo.get_active_by_user(user_id=current_user.id)
        if interview is None:
            raise fastapi.HTTPException(status_code=fastapi.status.HTTP_400_BAD_REQUEST, detail="No active interview to generate questions for")

    # Check if questions already exist for this interview (idempotent)
    existing = await question_repo.list_by_interview(interview_id=interview.id)
    persisted = existing
    cached = bool(existing)
    
    if not existing:
        # Generate questions only if they don't exist
        # Use resume context if present on user and use_resume is True
        resume_context = getattr(current_user, "resume_text", None) if payload.use_resume else None
        # Build influence knobs
        years = getattr(current_user, "years_experience", None)
        skills_json = getattr(current_user, "skills", None) or {}
        skills_list = list(skills_json.get("items", []) if isinstance(skills_json, dict) else [])
        has_resume = bool(resume_context)
        has_skills = bool(skills_list)

        role = syllabus_service._role_manager.derive_role(interview.track)
        topic_bank = syllabus_service.get_topics_for_role(role=role, difficulty=interview.difficulty)
        
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
            years_experience=years, 
            has_resume_text=has_resume, 
            has_skills=has_skills
        )
        
        # Convert QuestionRatio to dict format for backward compatibility
        ratio = {
            "tech": question_ratio.tech,
            "tech_allied": question_ratio.tech_allied,
            "behavioral": question_ratio.behavioral,
        }

        influence = {
            "target_role": role,               # Tech influence
            "difficulty": interview.difficulty, # Tech influence
            "experience_years": years,         # Tech-allied influence
            "skills": skills_list,             # Tech influence
            "headline": getattr(current_user, "target_position", None),  # Tech-allied influence
        }

        # For easy difficulty, use static pre-defined questions instead of LLM generation
        if interview.difficulty == "easy":
            static_items = get_static_questions(role=role, count=5, ratio=ratio)
            questions = [item["text"] for item in static_items]
            llm_error = None
            latency_ms = 0
            llm_model = "static"
            items = static_items
        else:
            # For medium, hard, expert: use LLM generation
            questions, llm_error, latency_ms, llm_model, items = await generate_interview_questions_with_llm(
                track=interview.track,
                context_text=resume_context,
                count=5,
                difficulty=interview.difficulty,
                syllabus_topics=topics,
                ratio=ratio,
                influence=influence,
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
                questions_data.append({
                    "text": item.get("text", ""),
                    "topic": item.get("topic"),
                    "category": item.get("category")
                })
        else:  # Fallback to plain question strings
            for question in questions:
                questions_data.append({
                    "text": question,
                    "topic": None,
                    "category": None
                })
        
        persisted = await question_repo.create_batch(
            interview_id=interview.id,
            questions_data=questions_data,
            resume_used=payload.use_resume
        )
        
        # Prepare response data for newly generated questions
        response_items = []
        for idx, question_obj in enumerate(persisted):
            structured = items[idx] if items and idx < len(items) else None
            response_items.append({
                "interviewQuestionId": question_obj.id,
                "text": (structured or {}).get("text") or question_obj.text,
                "topic": (structured or {}).get("topic") or question_obj.topic,
                "difficulty": (structured or {}).get("difficulty"),
                "category": (structured or {}).get("category") or question_obj.category,
                "isFollowUp": getattr(question_obj, "is_follow_up", False),
                "parentQuestionId": getattr(question_obj, "parent_question_id", None),
                "followUpStrategy": getattr(question_obj, "follow_up_strategy", None),
            })

        qs = {
            "questions": questions,
            "llm_error": llm_error,
            "latency_ms": latency_ms,
            "llm_model": llm_model,
            "items": response_items,
        }
    else:
        # Questions already exist, prepare response data from existing questions
        existing_items = [
            {
                "interviewQuestionId": q.id,
                "text": q.text,
                "topic": q.topic,
                "difficulty": None,
                "category": q.category,
                "isFollowUp": getattr(q, "is_follow_up", False),
                "parentQuestionId": getattr(q, "parent_question_id", None),
                "followUpStrategy": getattr(q, "follow_up_strategy", None),
            }
            for q in existing
        ]
        qs = {
            "questions": [q.text for q in existing],
            "llm_error": None,
            "latency_ms": None,
            "llm_model": None,
            "items": existing_items,
        }

    return GeneratedQuestionsInResponse(
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
    )


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
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
):
    interview = await interview_repo.get_by_id(interview_id=payload.interview_id)
    if interview is None or interview.user_id != current_user.id:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Interview not found")
    if interview.status == "completed":
        return {
            "id": interview.id,
            "status": interview.status,
            "message": "Interview already completed",
        }
    if interview.status != "active":
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_400_BAD_REQUEST, detail="Only active interviews can be completed")
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
        "message": "Interview marked as completed" if updated else "No update performed",
    }


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
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
) -> InterviewsListResponse:
    safe_limit = max(1, min(100, int(limit)))
    rows, next_cursor = await interview_repo.list_by_user_cursor(user_id=current_user.id, limit=safe_limit, cursor_id=cursor)
    
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
                    knowledge_percentage = score_summary.get("knowledgeCompetence", {}).get("percentage")
                    speech_fluency_percentage = score_summary.get("speechAndStructure", {}).get("percentage")
                # Fall back to old format (metrics)
                elif "metrics" in latest_report.report_json:
                    metrics = latest_report.report_json.get("metrics", {})
                    knowledge_competence = metrics.get("knowledgeCompetence", {})
                    speech_structure_fluency = metrics.get("speechStructure", {})
                    
                    knowledge_percentage = knowledge_competence.get("averagePct")
                    speech_fluency_percentage = speech_structure_fluency.get("averagePct")
        
        item = InterviewItem(
            interview_id=interview.id,
            track=interview.track,
            difficulty=interview.difficulty,
            status=interview.status,
            created_at=interview.created_at,
            knowledge_percentage=knowledge_percentage,
            speech_fluency_percentage=speech_fluency_percentage,
            attempts_count=attempts_count,
            resume_used=resume_used
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
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
    question_repo: InterviewQuestionCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewQuestionCRUDRepository)),
    question_attempt_repo: QuestionAttemptCRUDRepository = fastapi.Depends(get_repository(repo_type=QuestionAttemptCRUDRepository)),
) -> QuestionsListResponse:
    interview = await interview_repo.get_by_id(interview_id=interview_id)
    if interview is None or interview.user_id != current_user.id:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Interview not found")
    
    safe_limit = max(1, min(100, int(limit)))
    items, next_cursor = await question_repo.list_by_interview_cursor(interview_id=interview_id, limit=safe_limit, cursor_id=cursor)
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
            ) for q in items
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
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
    question_repo: QuestionAttemptCRUDRepository = fastapi.Depends(get_repository(repo_type=QuestionAttemptCRUDRepository)),
) -> QuestionAttemptsListResponse:
    interview = await interview_repo.get_by_id(interview_id=interview_id)
    if interview is None or interview.user_id != current_user.id:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Interview not found")
    safe_limit = max(1, min(100, int(limit)))
    items, next_cursor = await question_repo.list_by_interview_cursor(interview_id=interview_id, limit=safe_limit, cursor_id=cursor)
    return QuestionAttemptsListResponse(
        interview_id=interview_id,
        items=[QuestionAttemptItem(
            question_attempt_id=q.id,
            question_text=q.question_text,
            question_id=q.question_id,
            audio_url=q.audio_url,
            transcription=strip_word_level_data(q.transcription),
            created_at=q.created_at
        ) for q in items],
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
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
    question_repo: InterviewQuestionCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewQuestionCRUDRepository)),
    attempt_repo: QuestionAttemptCRUDRepository = fastapi.Depends(get_repository(repo_type=QuestionAttemptCRUDRepository)),
) -> CreateAttemptResponse:
    interview_id = payload.interview_id
    question_id = payload.question_id
    # Verify interview exists and belongs to user
    interview = await interview_repo.get_by_id(interview_id=interview_id)
    if interview is None or interview.user_id != current_user.id:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Interview not found")

    # Verify question exists and belongs to the interview
    question = await question_repo.get_by_id(question_id=question_id)
    if question is None or question.interview_id != interview_id:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Question not found")

    # Update question status to in_progress
    await question_repo.update_status(question_id=question_id, status="in_progress")

    # Create the question attempt
    attempt = await attempt_repo.create_attempt(
        interview_id=interview_id,
        question_id=question_id,
        question_text=question.text
    )

    return CreateAttemptResponse(question_attempt_id=attempt.id)


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
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
) -> InterviewsListWithSummaryResponse:
    safe_limit = max(1, min(100, int(limit)))
    
    # Add timeout to prevent hanging on slow database queries
    try:
        rows, next_cursor = await asyncio.wait_for(
            interview_repo.list_by_user_cursor_with_summary(user_id=current_user.id, limit=safe_limit, cursor_id=cursor),
            timeout=30.0  # 30 second timeout
        )
    except asyncio.TimeoutError:
        logger.error(f"Timeout fetching interviews with summary for user {current_user.id}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Request timeout while fetching interviews. Please try again."
        )
    except Exception as e:
        logger.error(f"Error fetching interviews with summary for user {current_user.id}: {e}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error fetching interviews"
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
                                speech_fluency = overall_feedback.get("speechFluency", {})
                                if isinstance(speech_fluency, dict):
                                    actionable_steps = speech_fluency.get("actionableSteps", [])
                                    if isinstance(actionable_steps, list):
                                        # New format has {title, description} objects
                                        for step in actionable_steps[:3]:
                                            if isinstance(step, dict) and "title" in step:
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
                    logger.warning(f"Error parsing report_json for interview {interview.id}: {e}")
            
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
                top_action_items=top_action_items
            )
            items.append(item)
        except Exception as e:
            # If there's any error processing this interview, log it and skip it
            logger.error(f"Error processing interview {interview.id} in list_my_interviews_with_summary: {e}")
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
    summary="Resume interview - get questions without attempts",
    description=(
        "Fetches questions from an interview that don't have any QuestionAttempt records. "
        "This is useful for resuming an interview where some questions have been answered."
    ),
)
async def resume_interview(
    request: ResumeInterviewRequest,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
    question_repo: InterviewQuestionCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewQuestionCRUDRepository)),
    question_attempt_repo: QuestionAttemptCRUDRepository = fastapi.Depends(get_repository(repo_type=QuestionAttemptCRUDRepository)),
) -> ResumeInterviewResponse:
    # Verify interview belongs to current user
    interview = await interview_repo.get_by_id_and_user(request.interview_id, current_user.id)
    if not interview:
        raise fastapi.HTTPException(status_code=404, detail="Interview not found or access denied")

    # Get questions without attempts
    questions_without_attempts = await question_repo.get_questions_without_attempts(interview_id=interview.id)
    
    # Get total questions count
    all_questions = await question_repo.list_by_interview(interview_id=interview.id)
    total_questions = len(all_questions)
    
    # Get count of questions with attempts
    attempted_count = await question_repo.get_questions_with_attempts_count(interview_id=interview.id)

    # Backfill any missing follow-up parent pointers
    await _backfill_follow_up_parents(
        questions=questions_without_attempts,
        question_repo=question_repo,
        question_attempt_repo=question_attempt_repo,
    )

    supplement_map = await _get_supplement_map(
        interview_id=interview.id,
        async_session=question_repo.async_session,
        ensure_generate=True,
        track=interview.track,
    )
    
    # Convert to response format
    question_items = [
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
        for q in questions_without_attempts
    ]

    return ResumeInterviewResponse(
        interview_id=interview.id,
        track=interview.track,
        difficulty=interview.difficulty,
        questions=question_items,
        total_questions=total_questions,
        attempted_questions=attempted_count,
        remaining_questions=len(questions_without_attempts),
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
            supplements = await supplement_service.get_for_interview(interview_id=interview_id)
    except SQLAlchemyError as exc:
        logger.warning("Supplements unavailable for interview %s: %s", interview_id, exc)
        return {}
    return {supp.interview_question_id: serialize_question_supplement(supp) for supp in supplements}


async def _backfill_follow_up_parents(
    *,
    questions: list,
    question_repo: InterviewQuestionCRUDRepository,
    question_attempt_repo: QuestionAttemptCRUDRepository,
) -> None:
    """Ensure follow-up questions always have a parent_question_id."""
    for q in questions:
        if getattr(q, "is_follow_up", False) and not getattr(q, "parent_question_id", None):
            attempt = await question_attempt_repo.get_first_by_question_id(question_id=q.id)
            parent_id = None
            if attempt and isinstance(attempt.analysis_json, dict):
                parent_id = attempt.analysis_json.get("follow_up", {}).get("parent_question_id")
            if parent_id:
                await question_repo.set_parent_question(question_id=q.id, parent_question_id=int(parent_id))
                q.parent_question_id = int(parent_id)
