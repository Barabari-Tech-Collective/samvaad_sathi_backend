import fastapi
import logging
import re
from fastapi import Form, UploadFile, File
from sqlalchemy.exc import SQLAlchemyError

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.repository import get_repository
from src.models.schemas.interview import (
    InterviewCreate,
    InterviewInResponse,
    GeneratedQuestionsInResponse,
    StructurePracticeQuestionsResponse,
    GenerateQuestionsRequest,
    GenerateNonTechQuestionsRequest,
    QuestionItem,
    QuestionItemWithHint,
    QuestionSupplementOut,
    QuestionSupplementsResponse,
)
from src.models.schemas.pronunciation import (
    PronunciationPracticeCreate,
    PronunciationPracticeResponse,
)
from src.models.schemas.structure_practice import (
    StructurePracticeSessionCreate,
    StructurePracticeSessionResponse,
    StructurePracticeAnswerSubmitResponse,
    StructurePracticeAnalysisResponse,
    FrameworkProgress,
    FrameworkSection,
    TimePerSection,
)
from src.repository.crud.interview import InterviewCRUDRepository
from src.repository.crud.interview_question import InterviewQuestionCRUDRepository
from src.repository.crud.question import QuestionAttemptCRUDRepository
from src.repository.crud.job_profile import JobProfileCRUDRepository
from src.repository.crud.pronunciation_practice import PronunciationPracticeCRUDRepository
from src.repository.crud.structure_practice import (
    StructurePracticeCRUDRepository,
    StructurePracticeAnswerCRUDRepository,
)
from src.services.llm import generate_interview_questions_with_llm
from src.services.static_questions import get_static_questions
from src.services.full_stack_interview_questions import generate_full_stack_questions_with_llm
from src.services.syllabus_service import syllabus_service
from src.services.question_supplements import (
    QuestionSupplementService,
    serialize_question_supplement,
)
from src.services.structure_hints import generate_structure_hints_for_questions
from src.services.pronunciation_tts import generate_pronunciation_audio
from src.services.structure_analysis import analyze_structure_answer
from src.services.audio_processor import validate_audio_file, save_audio_file, cleanup_temp_audio_file
from src.services.progressive_hints import (
    detect_framework,
    get_framework_sections,
    get_initial_hint,
)
from src.services.non_tech_blueprint import (
    NON_TECH_BLUEPRINT_VERSION,
    NON_TECH_FIXED_DIFFICULTY,
    select_non_tech_interview_questions,
)
from src.services.whisper import transcribe_audio_with_whisper, validate_transcription_language
from src.services.analytics_events import track_analytics_event

logger = logging.getLogger(__name__)
FOLLOW_UP_STRATEGY = "llm_transcription_based"
FULL_STACK_ROLE = "Full Stack Developer"


router = fastapi.APIRouter(prefix="/v2", tags=["interviews-v2"])


def _supplements_enabled_for_track(track: str | None) -> bool:
    normalized_track = (track or "").strip().lower()
    return not normalized_track.startswith("non-tech:")


def _normalize_non_tech_job_name(job_name: str | None) -> str:
    raw = (job_name or "").strip()
    if not raw:
        return "General Role"
    collapsed = re.sub(r"\s+", " ", raw)
    return collapsed.title()


def _get_cached_structure_practice_response() -> StructurePracticeQuestionsResponse:
    """
    Returns a fixed/cached response with generic practice questions and structure hints.
    Used when no interview_id is provided to the structure-practice endpoint.
    """
    cached_items = [
        QuestionItemWithHint(
            interview_question_id=0,
            text="Explain the concept of closures in JavaScript and provide a practical use case where closures are particularly useful.",
            topic="Closures in JavaScript",
            difficulty=None,
            category="tech",
            is_follow_up=False,
            parent_question_id=None,
            follow_up_strategy=None,
            supplement=None,
            structure_hint="Use C-T-E-T-D: explain the context of scope in JS, define closure theory, show a practical example, discuss trade-offs like memory considerations, and conclude with when to use closures."
        ),
        QuestionItemWithHint(
            interview_question_id=0,
            text="What is the difference between 'let', 'const', and 'var' in JavaScript? When would you choose one over the other?",
            topic="Variable Declarations in JavaScript",
            difficulty=None,
            category="tech",
            is_follow_up=False,
            parent_question_id=None,
            follow_up_strategy=None,
            supplement=None,
            structure_hint="Apply C-T-E-T-D: set context on variable scoping, explain the theory behind each keyword, provide examples of their differences, discuss trade-offs like hoisting and mutability, and decide when to use each."
        ),
        QuestionItemWithHint(
            interview_question_id=0,
            text="Describe a situation where you had to optimize the performance of a web application. What approach did you take and what was the outcome?",
            topic="Performance Optimization",
            difficulty=None,
            category="behavioral",
            is_follow_up=False,
            parent_question_id=None,
            follow_up_strategy=None,
            supplement=None,
            structure_hint="Use STAR: describe the Situation (slow application), specify your Task (optimize performance), detail your Actions (profiling, identifying bottlenecks, implementing fixes), and summarize the Result (measurable improvements)."
        ),
        QuestionItemWithHint(
            interview_question_id=0,
            text="How does the event loop work in Node.js? Explain with the phases of the event loop.",
            topic="Node.js Event Loop",
            difficulty=None,
            category="tech",
            is_follow_up=False,
            parent_question_id=None,
            follow_up_strategy=None,
            supplement=None,
            structure_hint="Follow C-T-E-T-D: provide context on async programming in Node.js, explain event loop theory and its phases, give an example of callback execution order, discuss trade-offs of blocking operations, and conclude on best practices."
        ),
        QuestionItemWithHint(
            interview_question_id=0,
            text="Tell me about a time when you had to learn a new technology quickly to complete a project. How did you approach the learning process?",
            topic="Learning and Adaptability",
            difficulty=None,
            category="behavioral",
            is_follow_up=False,
            parent_question_id=None,
            follow_up_strategy=None,
            supplement=None,
            structure_hint="Use STAR: describe the Situation (new technology requirement), your Task (learn and apply it), your Actions (learning strategy, resources used, practice), and the Result (successful implementation, lessons learned)."
        ),
    ]
    
    return StructurePracticeQuestionsResponse(
        interview_id=None,
        track="Software Development",
        count=len(cached_items),
        questions=[item.text for item in cached_items],
        question_ids=None,
        items=cached_items,
        llm_model=None,
        llm_latency_ms=None,
        llm_error=None,
        cached=True,
    )


@router.post(
    path="/interviews/create",
    name="interviews-v2:create",
    response_model=InterviewInResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Create or resume a V2 interview session",
)
async def create_or_resume_interview_v2(
    payload: InterviewCreate,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
) -> InterviewInResponse:
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
        
    if payload.track == FULL_STACK_ROLE and difficulty == "medium" and getattr(payload, "use_resume", True):
        resume_text = getattr(current_user, "resume_text", None)
        if not resume_text or not str(resume_text).strip():
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_400_BAD_REQUEST, 
                detail="Cannot use resume for interview: No resume is saved to your profile."
            )

    interview = await interview_repo.create_interview(user_id=current_user.id, track=payload.track, difficulty=difficulty)
    await track_analytics_event(
        interview_repo.async_session,
        event_type="interview_started",
        user_id=current_user.id,
        interview_id=interview.id,
        event_data={"track": interview.track, "difficulty": interview.difficulty, "api": "interviews_v2.create"},
    )
    await track_analytics_event(
        interview_repo.async_session,
        event_type="role_selected",
        user_id=current_user.id,
        interview_id=interview.id,
        event_data={"track": interview.track, "source": "interviews_v2.create"},
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
    name="interviews-v2:generate-questions",
    response_model=GeneratedQuestionsInResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Generate 5-question adaptive interview set",
)
async def generate_questions_v2(
    payload: GenerateQuestionsRequest,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
    question_repo: InterviewQuestionCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewQuestionCRUDRepository)),
    question_attempt_repo: QuestionAttemptCRUDRepository = fastapi.Depends(get_repository(repo_type=QuestionAttemptCRUDRepository)),
) -> GeneratedQuestionsInResponse:
    supplement_service = QuestionSupplementService(async_session=question_repo.async_session)
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

    import sqlalchemy
    # Acquire advisory lock on interview ID to prevent concurrent generation
    # Using bound parameters for production-level SQL injection prevention
    await question_repo.async_session.execute(
        sqlalchemy.text("SELECT pg_advisory_xact_lock(:id)").bindparams(id=interview.id)
    )

    existing = await question_repo.list_by_interview(interview_id=interview.id)
    persisted = existing
    cached = bool(existing)

    if not existing:
        resume_context = getattr(current_user, "resume_text", None) if payload.use_resume else None
        years = getattr(current_user, "years_experience", None)
        skills_json = getattr(current_user, "skills", None) or {}
        skills_list = list(skills_json.get("items", []) if isinstance(skills_json, dict) else [])
        has_resume = bool(resume_context)
        has_skills = bool(skills_list)

        if interview.track == FULL_STACK_ROLE and interview.difficulty == "medium" and not has_resume:
            qs = {
                "questions": ["Before proceeding with a Medium level Full Stack Developer interview, please add your resume."],
                "llm_error": None,
                "latency_ms": 0,
                "llm_model": "static",
                "items": [{"text": "Before proceeding with a Medium level Full Stack Developer interview, please add your resume.", "topic": "System", "category": "system", "isFollowUp": False, "parentQuestionId": None, "followUpStrategy": None, "supplement": None, "difficulty": interview.difficulty, "interviewQuestionId": 0}]
            }
            return GeneratedQuestionsInResponse(
                interview_id=interview.id,
                track=interview.track,
                count=1,
                questions=qs["questions"],
                question_ids=[0],
                items=[
                    QuestionItem(
                        interview_question_id=item.get("interviewQuestionId"),
                        text=item.get("text", ""),
                        topic=item.get("topic"),
                        difficulty=item.get("difficulty"),
                        category=item.get("category"),
                        is_follow_up=item.get("isFollowUp", False),
                        parent_question_id=item.get("parentQuestionId"),
                        follow_up_strategy=item.get("followUpStrategy"),
                        supplement=item.get("supplement"),
                    )
                    for item in qs.get("items", [])
                ],
                cached=False,
                llm_model=qs.get("llm_model"),
                llm_latency_ms=qs.get("latency_ms"),
                llm_error=qs.get("llm_error"),
            )

        question_count = 7 if interview.track == FULL_STACK_ROLE else 5  # Base questions, leaves room for dynamic follow-ups

        questions, llm_error, latency_ms, llm_model, items = await generate_full_stack_questions_with_llm(
            domain=interview.track,
            years_experience=years,
            difficulty=interview.difficulty,
            count=question_count,
            seed=f"{current_user.id}:{interview.id}"
        )

        if not questions:
            questions = [
                f"Walk me through a complex challenge you solved in {interview.track}.",
                f"How do you evaluate success in {interview.track} projects?",
                f"Describe a time you debugged a difficult issue in {interview.track}.",
                f"Explain an architecture decision you made recently.",
                "Tell me about a situation where you had to defend a technical decision.",
            ]

        questions_data: list[dict[str, object]] = []
        if items:
            for item in items:
                questions_data.append(
                    {
                        "text": item.get("text", ""),
                        "topic": item.get("topic"),
                        "category": item.get("category"),
                        "follow_up_strategy": item.get("followUpStrategy") or FOLLOW_UP_STRATEGY,
                    }
                )
        else:
            for question in questions:
                questions_data.append({
                    "text": question, 
                    "topic": None, 
                    "category": None, 
                    "follow_up_strategy": FOLLOW_UP_STRATEGY
                })

        # Double check to prevent race condition double-insertion
        existing_again = await question_repo.list_by_interview(interview_id=interview.id)
        if existing_again:
            logger.warning(f"Race condition detected: questions already exist for interview {interview.id}. Skipping insert.")
            persisted = existing_again
        else:
            persisted = await question_repo.create_batch(
                interview_id=interview.id,
                questions_data=questions_data,
                resume_used=payload.use_resume,
            )

        supplements_map = await _get_supplement_map(
            interview_id=interview.id,
            supplement_service=supplement_service,
            ensure_generate=True,
            track=interview.track,
        )
        _validate_supplements_response(items=supplements_map, source="generate-questions")
        response_items: list[dict[str, object]] = []
        for idx, question_obj in enumerate(persisted):
            structured = items[idx] if items and idx < len(items) else None
            response_items.append(
                {
                    "interviewQuestionId": question_obj.id,
                    "text": (structured or {}).get("text") or question_obj.text,
                    "topic": (structured or {}).get("topic") or question_obj.topic,
                    "difficulty": (structured or {}).get("difficulty"),
                    "category": (structured or {}).get("category") or question_obj.category,
                    "isFollowUp": question_obj.is_follow_up,
                    "parentQuestionId": question_obj.parent_question_id,
                    "followUpStrategy": question_obj.follow_up_strategy,
                    "supplement": supplements_map.get(question_obj.id),
                }
            )
        qs = {
            "questions": [q["text"] for q in questions_data],
            "llm_error": llm_error,
            "latency_ms": latency_ms,
            "llm_model": llm_model,
            "items": response_items,
        }
    else:
        # For cached/interrupted interviews, ensure follow-up questions have parent pointers
        await _backfill_follow_up_parents(
            questions=existing,
            question_repo=question_repo,
            question_attempt_repo=question_attempt_repo,
        )
        supplements_map = await _get_supplement_map(
            interview_id=interview.id,
            supplement_service=supplement_service,
            ensure_generate=True,
            track=interview.track,
        )
        _validate_supplements_response(items=supplements_map, source="generate-questions-cached")
        response_items = [
            {
                "interviewQuestionId": q.id,
                "text": q.text,
                "topic": q.topic,
                "difficulty": None,
                "category": q.category,
                "isFollowUp": q.is_follow_up,
                "parentQuestionId": q.parent_question_id,
                "followUpStrategy": q.follow_up_strategy,
                "supplement": supplements_map.get(q.id),
            }
            for q in existing
        ]
        qs = {
            "questions": [q.text for q in existing],
            "llm_error": None,
            "latency_ms": None,
            "llm_model": None,
            "items": response_items,
        }

    return GeneratedQuestionsInResponse(
        interview_id=interview.id,
        track=interview.track,
        count=len(persisted),
        questions=[q.text for q in persisted],
        question_ids=[q.id for q in persisted],
        items=[QuestionItem(
            interview_question_id=item.get("interviewQuestionId"),
            text=item.get("text", ""),
            topic=item.get("topic"),
            difficulty=item.get("difficulty"),
            category=item.get("category"),
            is_follow_up=item.get("isFollowUp", False),
            parent_question_id=item.get("parentQuestionId"),
            follow_up_strategy=item.get("followUpStrategy"),
            supplement=item.get("supplement"),
        ) for item in qs.get("items", [])],
        cached=cached,
        llm_model=qs.get("llm_model"),
        llm_latency_ms=qs.get("latency_ms"),
        llm_error=qs.get("llm_error"),
    )


@router.post(
    path="/interviews/non-tech/generate-questions",
    name="interviews-v2:generate-non-tech-questions",
    response_model=GeneratedQuestionsInResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Generate non-tech interview questions from selected job profile",
)
async def generate_non_tech_questions_v2(
    payload: GenerateNonTechQuestionsRequest,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
    question_repo: InterviewQuestionCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewQuestionCRUDRepository)),
    question_attempt_repo: QuestionAttemptCRUDRepository = fastapi.Depends(get_repository(repo_type=QuestionAttemptCRUDRepository)),
    job_profile_repo: JobProfileCRUDRepository = fastapi.Depends(get_repository(repo_type=JobProfileCRUDRepository)),
) -> GeneratedQuestionsInResponse:
    supplement_service = QuestionSupplementService(async_session=question_repo.async_session)
    try:
        job_profile = await job_profile_repo.get_by_id(job_profile_id=payload.job_profile_id)
    except SQLAlchemyError as exc:
        msg = str(exc).lower()
        if "job_profile" in msg and ("does not exist" in msg or "undefinedtable" in msg):
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Non-tech interview feature unavailable until migration 'job_profile_001' is applied",
            )
        raise
    if job_profile is None:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Job profile not found")

    requested_difficulty = (payload.difficulty or "").lower().strip() if payload.difficulty else None
    difficulty = NON_TECH_FIXED_DIFFICULTY

    normalized_job_name = _normalize_non_tech_job_name(job_profile.job_name)
    track = f"Non-Tech: {normalized_job_name}"
    interview = await interview_repo.get_active_by_user(user_id=current_user.id)
    if interview is None or interview.track != track:
        interview = await interview_repo.create_interview(
            user_id=current_user.id,
            track=track,
            difficulty=difficulty,
        )
        await track_analytics_event(
            interview_repo.async_session,
            event_type="non_tech_interview_started",
            user_id=current_user.id,
            interview_id=interview.id,
            event_data={
                "track": track,
                "normalized_job_name": normalized_job_name,
                "difficulty": difficulty,
                "requested_difficulty": requested_difficulty,
                "job_profile_id": job_profile.id,
                "blueprint_version": NON_TECH_BLUEPRINT_VERSION,
                "api": "interviews_v2.generate_non_tech_questions",
            },
        )

    existing = await question_repo.list_by_interview(interview_id=interview.id)
    persisted = existing
    cached = bool(existing)

    if not existing:
        questions_data = select_non_tech_interview_questions(
            role_name=normalized_job_name,
            company_name=job_profile.company_name,
            seed=f"{current_user.id}:{job_profile.id}:{interview.id}",
        )

        if payload.use_resume and isinstance(getattr(current_user, "resume_text", None), str):
            resume_context = (getattr(current_user, "resume_text", None) or "").strip()
            if resume_context:
                questions_data[0]["text"] = f"Based on your background, {questions_data[0]['text']}"

        if job_profile.additional_context:
            context_suffix = job_profile.additional_context.strip()
            if context_suffix:
                questions_data[0]["text"] = f"{questions_data[0]['text']} ({context_suffix})"

        for idx in range(min(2, len(questions_data))):
            questions_data[idx]["follow_up_strategy"] = FOLLOW_UP_STRATEGY

        persisted = await question_repo.create_batch(
            interview_id=interview.id,
            questions_data=questions_data,
            resume_used=payload.use_resume,
        )

        supplements_map = await _get_supplement_map(
            interview_id=interview.id,
            supplement_service=supplement_service,
            ensure_generate=True,
            track=interview.track,
        )
        _validate_supplements_response(items=supplements_map, source="generate-non-tech-questions")
        response_items: list[dict[str, object]] = []
        for question_obj in persisted:
            response_items.append(
                {
                    "interviewQuestionId": question_obj.id,
                    "text": question_obj.text,
                    "topic": question_obj.topic,
                    "difficulty": difficulty,
                    "category": question_obj.category,
                    "isFollowUp": question_obj.is_follow_up,
                    "parentQuestionId": question_obj.parent_question_id,
                    "followUpStrategy": question_obj.follow_up_strategy,
                    "supplement": supplements_map.get(question_obj.id),
                }
            )
        qs = {
            "questions": [q["text"] for q in questions_data],
            "llm_error": None,
            "latency_ms": 0,
            "llm_model": f"blueprint_static_{NON_TECH_BLUEPRINT_VERSION}",
            "items": response_items,
        }
    else:
        await _backfill_follow_up_parents(
            questions=existing,
            question_repo=question_repo,
            question_attempt_repo=question_attempt_repo,
        )
        supplements_map = await _get_supplement_map(
            interview_id=interview.id,
            supplement_service=supplement_service,
            ensure_generate=True,
            track=interview.track,
        )
        _validate_supplements_response(items=supplements_map, source="generate-non-tech-questions-cached")
        response_items = [
            {
                "interviewQuestionId": q.id,
                "text": q.text,
                "topic": q.topic,
                "difficulty": difficulty,
                "category": q.category,
                "isFollowUp": q.is_follow_up,
                "parentQuestionId": q.parent_question_id,
                "followUpStrategy": q.follow_up_strategy,
                "supplement": supplements_map.get(q.id),
            }
            for q in existing
        ]
        qs = {
            "questions": [q.text for q in existing],
            "llm_error": None,
            "latency_ms": None,
            "llm_model": None,
            "items": response_items,
        }

    return GeneratedQuestionsInResponse(
        interview_id=interview.id,
        track=interview.track,
        count=len(persisted),
        questions=[q.text for q in persisted],
        question_ids=[q.id for q in persisted],
        items=[
            QuestionItem(
                interview_question_id=item.get("interviewQuestionId"),
                text=item.get("text", ""),
                topic=item.get("topic"),
                difficulty=item.get("difficulty"),
                category=item.get("category"),
                is_follow_up=item.get("isFollowUp", False),
                parent_question_id=item.get("parentQuestionId"),
                follow_up_strategy=item.get("followUpStrategy"),
                supplement=item.get("supplement"),
            )
            for item in qs.get("items", [])
        ],
        cached=cached,
        llm_model=qs.get("llm_model"),
        llm_latency_ms=qs.get("latency_ms"),
        llm_error=qs.get("llm_error"),
    )


@router.post(
    path="/interviews/structure-practice",
    name="interviews-v2:structure-practice",
    response_model=StructurePracticeQuestionsResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Get interview questions with structure hints for practice",
)
async def get_structure_practice_questions(
    interview_id: int | None = fastapi.Body(None, embed=True),
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
    question_repo: InterviewQuestionCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewQuestionCRUDRepository)),
) -> StructurePracticeQuestionsResponse:
    """
    Fetch existing interview questions with AI-generated structure hints.
    Questions and supplements are fetched from DB, only hints are newly generated.
    If interview_id is not provided, returns cached generic practice questions.
    """
    # Return cached response if no interview_id provided
    if interview_id is None:
        return _get_cached_structure_practice_response()
    
    # Validate interview ownership
    interview = await interview_repo.get_by_id(interview_id=interview_id)
    if interview is None or interview.user_id != current_user.id:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Interview not found"
        )
    
    # Get existing questions from database
    questions = await question_repo.list_by_interview(interview_id=interview.id)
    if not questions:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail="No questions found for this interview. Generate questions first."
        )
    
    # Get supplements
    supplement_service = QuestionSupplementService(async_session=question_repo.async_session)
    supplements_map = await _get_supplement_map(
        interview_id=interview.id,
        supplement_service=supplement_service,
        ensure_generate=False,  # Don't generate, just fetch existing
        track=interview.track,
    )
    
    # Prepare questions data for hint generation
    questions_data = [
        {
            "text": q.text,
            "topic": q.topic,
            "category": q.category,
        }
        for q in questions
    ]
    
    # Generate structure hints using LLM
    hints_map, llm_error, latency_ms, llm_model = await generate_structure_hints_for_questions(
        questions=questions_data,
        track=interview.track,
        difficulty=interview.difficulty,
    )
    
    # Build response items with hints
    items_with_hints = []
    for q in questions:
        hint = hints_map.get(q.text, "Break down your answer logically with clear examples and explain your reasoning.")
        items_with_hints.append(
            QuestionItemWithHint(
                interview_question_id=q.id,
                text=q.text,
                topic=q.topic,
                difficulty=None,
                category=q.category,
                is_follow_up=q.is_follow_up,
                parent_question_id=q.parent_question_id,
                follow_up_strategy=q.follow_up_strategy,
                supplement=supplements_map.get(q.id),
                structure_hint=hint,
            )
        )
    
    return StructurePracticeQuestionsResponse(
        interview_id=interview.id,
        track=interview.track,
        count=len(questions),
        questions=[q.text for q in questions],
        question_ids=[q.id for q in questions],
        items=items_with_hints,
        llm_model=llm_model,
        llm_latency_ms=latency_ms,
        llm_error=llm_error,
        cached=False,
    )


@router.post(
    path="/interviews/{interview_id}/supplements",
    name="interviews-v2:generate-supplements",
    response_model=QuestionSupplementsResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Generate supplemental code/diagram snippets for interview questions",
)
async def generate_supplements_v2(
    interview_id: int,
    regenerate: bool = fastapi.Query(
        default=False,
        description="If true, overwrite existing supplements with new LLM output",
    ),
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
) -> QuestionSupplementsResponse:
    interview = await interview_repo.get_by_id(interview_id=interview_id)
    if interview is None or interview.user_id != current_user.id:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Interview not found")
    if not _supplements_enabled_for_track(interview.track):
        return QuestionSupplementsResponse(interview_id=interview.id, supplements=[])

    supplement_service = QuestionSupplementService(async_session=interview_repo.async_session)
    supplements = await supplement_service.generate_for_interview(
        interview_id=interview.id,
        regenerate=regenerate,
    )
    return QuestionSupplementsResponse(
        interview_id=interview.id,
        supplements=[serialize_question_supplement(s) for s in supplements],
    )


@router.get(
    path="/interviews/{interview_id}/supplements",
    name="interviews-v2:get-supplements",
    response_model=QuestionSupplementsResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Fetch supplemental snippets for interview questions",
)
async def get_supplements_v2(
    interview_id: int,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
) -> QuestionSupplementsResponse:
    interview = await interview_repo.get_by_id(interview_id=interview_id)
    if interview is None or interview.user_id != current_user.id:
        raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Interview not found")
    if not _supplements_enabled_for_track(interview.track):
        return QuestionSupplementsResponse(interview_id=interview.id, supplements=[])

    supplement_service = QuestionSupplementService(async_session=interview_repo.async_session)
    supplements = await supplement_service.get_for_interview(interview_id=interview.id)
    return QuestionSupplementsResponse(
        interview_id=interview.id,
        supplements=[serialize_question_supplement(s) for s in supplements],
    )


async def _get_supplement_map(
    *,
    interview_id: int,
    supplement_service: QuestionSupplementService,
    ensure_generate: bool = False,
    track: str | None = None,
) -> dict[int, QuestionSupplementOut]:
    if not _supplements_enabled_for_track(track):
        return {}
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


def _validate_supplements_response(*, items: dict[int, QuestionSupplementOut], source: str) -> None:
    """Validate supplements returned from LLM before sending to clients."""
    if not items:
        return
    allowed_types = {"code", "diagram"}
    mermaid_starters = ("flowchart", "graph", "sequenceDiagram", "stateDiagram", "classDiagram")

    for qid, supp in items.items():
        stype = (supp.supplement_type or "").lower()
        fmt = (supp.format or "").lower() if supp.format else ""
        content = supp.content or ""
        if stype not in allowed_types:
            logger.warning("Invalid supplement type (%s) for question %s from %s", stype, qid, source)
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid supplement type generated",
            )
        if stype == "diagram":
            if fmt != "mermaid":
                logger.warning("Diagram supplement missing mermaid format for question %s from %s", qid, source)
                raise fastapi.HTTPException(
                    status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Invalid diagram supplement format",
                )
            stripped = content.strip()
            if not stripped.startswith("```mermaid"):
                if not any(stripped.startswith(prefix) for prefix in mermaid_starters):
                    logger.warning("Mermaid supplement failed syntax precheck for question %s from %s", qid, source)
                    raise fastapi.HTTPException(
                        status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Mermaid supplement failed syntax precheck",
                    )


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


# ==========================
# Pronunciation Practice APIs
# ==========================


@router.post(
    path="/pronunciation/create",
    name="pronunciation:create",
    response_model=PronunciationPracticeResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Create a pronunciation practice session with 10 random words",
)
async def create_pronunciation_practice(
    payload: PronunciationPracticeCreate,
    current_user=fastapi.Depends(get_current_user),
    pronunciation_repo: PronunciationPracticeCRUDRepository = fastapi.Depends(
        get_repository(repo_type=PronunciationPracticeCRUDRepository)
    ),
) -> PronunciationPracticeResponse:
    """
    Create a new pronunciation practice session.
    
    - Selects 10 random words from the specified difficulty level
    - Returns practice session ID and word list with phonetic guides
    """
    try:
        practice = await pronunciation_repo.create_practice_session(
            user_id=current_user.id,
            difficulty=payload.difficulty,
        )
        await track_analytics_event(
            pronunciation_repo.async_session,
            event_type="practice_started",
            user_id=current_user.id,
            event_data={"practice_type": "pronunciation", "difficulty": payload.difficulty},
        )
        
        # Convert words array to response format with indices
        from src.models.schemas.pronunciation import PronunciationWord
        words = [
            PronunciationWord(
                index=idx,
                word=word_obj["word"],
                phonetic=word_obj["phonetic"]
            )
            for idx, word_obj in enumerate(practice.words)
        ]
        
        return PronunciationPracticeResponse(
            practice_id=practice.id,
            difficulty=practice.difficulty,
            words=words,
            total_words=len(words),
            status=practice.status,
            created_at=practice.created_at,
        )
    
    except ValueError as e:
        logger.error(f"Invalid difficulty level: {e}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error creating pronunciation practice: {e}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create pronunciation practice session",
        )


@router.get(
    path="/pronunciation/{practice_id}/audio/{question_number}",
    name="pronunciation:get-audio",
    summary="Get pronunciation audio for a specific word",
    responses={
        200: {
            "content": {"audio/ogg": {}},
            "description": "Returns audio file in Opus format",
        }
    },
)
async def get_pronunciation_audio(
    practice_id: int,
    question_number: int,
    slow: bool = fastapi.Query(False, description="Generate slow-paced audio for practice"),
    current_user=fastapi.Depends(get_current_user),
    pronunciation_repo: PronunciationPracticeCRUDRepository = fastapi.Depends(
        get_repository(repo_type=PronunciationPracticeCRUDRepository)
    ),
) -> fastapi.Response:
    """
    Get pronunciation audio for a specific word in a practice session.
    
    - **practice_id**: The pronunciation practice session ID
    - **question_number**: Index of the word (0-9)
    - **slow**: Whether to generate slow-paced audio (default: false)
    
    Returns optimized audio file in Opus format.
    """
    # Validate question number
    if question_number < 0 or question_number > 9:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail="question_number must be between 0 and 9",
        )
    
    # Get practice session
    practice = await pronunciation_repo.get_by_id_and_user(
        practice_id=practice_id,
        user_id=current_user.id,
    )
    
    if not practice:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Pronunciation practice session not found",
        )
    
    # Get the word at the specified index
    if question_number >= len(practice.words):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail=f"question_number {question_number} out of range for this practice session",
        )
    
    word_obj = practice.words[question_number]
    word = word_obj.get("word", "")
    
    if not word:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invalid word data in practice session",
        )
    
    # Generate audio using TTS service
    audio_bytes, error, latency_ms = await generate_pronunciation_audio(
        word=word,
        slow=slow,
    )
    
    if error:
        logger.error(f"TTS error for word '{word}': {error}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate pronunciation audio",
        )
    
    if not audio_bytes:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No audio generated",
        )
    
    # Return audio file with appropriate headers
    return fastapi.Response(
        content=audio_bytes,
        media_type="audio/ogg",
        headers={
            "Content-Disposition": f'inline; filename="pronunciation_{practice_id}_{question_number}{"_slow" if slow else ""}.ogg"',
            "X-Audio-Latency-Ms": str(latency_ms),
        },
    )


# ==================== Structure Practice Endpoints ====================


@router.post(
    path="/structure-practice/session",
    name="structure-practice:create-session",
    response_model=StructurePracticeSessionResponse,
    status_code=fastapi.status.HTTP_201_CREATED,
    summary="Create a new structure practice session",
)
async def create_structure_practice_session(
    request: StructurePracticeSessionCreate,
    current_user=fastapi.Depends(get_current_user),
    interview_repo: InterviewCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewCRUDRepository)),
    question_repo: InterviewQuestionCRUDRepository = fastapi.Depends(get_repository(repo_type=InterviewQuestionCRUDRepository)),
    structure_practice_repo: StructurePracticeCRUDRepository = fastapi.Depends(get_repository(repo_type=StructurePracticeCRUDRepository)),
) -> StructurePracticeSessionResponse:
    """
    Create a new structure practice session.
    If interview_id is provided, fetches questions from that interview.
    Otherwise, creates a new interview with generated questions.
    """
    if request.interview_id:
        # Validate interview ownership
        interview = await interview_repo.get_by_id(interview_id=request.interview_id)
        if interview is None or interview.user_id != current_user.id:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_404_NOT_FOUND,
                detail="Interview not found"
            )
        
        # Get existing questions
        questions = await question_repo.list_by_interview(interview_id=interview.id)
        if not questions:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_400_BAD_REQUEST,
                detail="No questions found for this interview. Generate questions first."
            )
        
        # Prepare questions data
        questions_data = [
            {
                "text": q.text,
                "topic": q.topic,
                "category": q.category,
            }
            for q in questions
        ]
        
        # Generate structure hints
        hints_map, _, _, _ = await generate_structure_hints_for_questions(
            questions=questions_data,
            track=interview.track,
            difficulty=interview.difficulty,
        )
        
        # Build questions list with framework info
        questions_list = [
            {
                "question_id": q.id,
                "text": q.text,
                "structure_hint": hints_map.get(q.text, "Structure your answer clearly with examples."),
                "framework": detect_framework(hints_map.get(q.text, "")),
                "index": idx,
            }
            for idx, q in enumerate(questions)
        ]
        
        # Add sections and current_section to each question
        for q in questions_list:
            framework = q["framework"]
            sections = get_framework_sections(framework)
            initial_hint = get_initial_hint(framework)
            q["sections"] = sections
            q["current_section"] = initial_hint["section_name"]
            q["current_hint"] = initial_hint["hint"]
        
        track = interview.track
    else:
        # Create a new interview with questions for structure practice
        track = request.track or "JavaScript Developer"
        difficulty = (request.difficulty or "easy").lower()
        if difficulty not in ("easy", "medium", "hard", "expert"):
            difficulty = "easy"
        
        # Create interview
        new_interview = await interview_repo.create_interview(
            user_id=current_user.id,
            track=track,
            difficulty=difficulty
        )
        
        # Generate questions based on difficulty
        role = syllabus_service._role_manager.derive_role(track)
        topic_bank = syllabus_service.get_topics_for_role(role=role, difficulty=difficulty)
        topics = {
            "tech": topic_bank.tech,
            "tech_allied": topic_bank.tech_allied,
            "behavioral": topic_bank.behavioral,
            "archetypes": topic_bank.archetypes,
            "depth_guidelines": topic_bank.depth_guidelines,
        }
        question_ratio = syllabus_service.compute_question_ratio(
            years_experience=None,
            has_resume_text=False,
            has_skills=False,
        )
        ratio = {
            "tech": question_ratio.tech,
            "tech_allied": question_ratio.tech_allied,
            "behavioral": question_ratio.behavioral,
        }
        
        question_count = 5
        
        # For easy difficulty, use static questions
        if difficulty == "easy":
            from src.services.static_questions import get_static_questions
            static_items = get_static_questions(role=role, count=question_count, ratio=ratio)
            questions_data = [
                {
                    "text": item["text"],
                    "topic": item.get("topic"),
                    "category": item.get("category"),
                }
                for item in static_items
            ]
        else:
            # For medium/hard/expert, generate with LLM
            questions, error, latency_ms, llm_model, structured_items = await generate_interview_questions_with_llm(
                track=track,
                context_text=None,
                count=question_count,
                difficulty=difficulty,
                syllabus_topics=topics,
                ratio=ratio,
                influence={},
            )
            
            if error or not structured_items:
                raise fastapi.HTTPException(
                    status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to generate questions: {error or 'No questions generated'}"
                )
            
            questions_data = [
                {
                    "text": item["text"],
                    "topic": item.get("topic"),
                    "category": item.get("category"),
                }
                for item in structured_items
            ]
        
        # Save questions to database using create_batch
        db_questions = await question_repo.create_batch(
            interview_id=new_interview.id,
            questions_data=questions_data,
            resume_used=False,
        )
        
        # Generate structure hints
        hints_map, _, _, _ = await generate_structure_hints_for_questions(
            questions=questions_data,
            track=track,
            difficulty=difficulty,
        )
        
        # Build questions list with framework info
        questions_list = [
            {
                "question_id": q.id,
                "text": q.text,
                "structure_hint": hints_map.get(q.text, "Structure your answer clearly with examples."),
                "framework": detect_framework(hints_map.get(q.text, "")),
                "index": idx,
            }
            for idx, q in enumerate(db_questions)
        ]
        
        # Add sections and current_section to each question
        for q in questions_list:
            framework = q["framework"]
            sections = get_framework_sections(framework)
            initial_hint = get_initial_hint(framework)
            q["sections"] = sections
            q["current_section"] = initial_hint["section_name"]
            q["current_hint"] = initial_hint["hint"]
        
        # Link the interview to the practice session
        request.interview_id = new_interview.id
    
    # Create practice session
    practice = await structure_practice_repo.create_practice_session(
        user_id=current_user.id,
        interview_id=request.interview_id,
        track=track,
        questions=questions_list,
    )
    await track_analytics_event(
        structure_practice_repo.async_session,
        event_type="practice_started",
        user_id=current_user.id,
        interview_id=practice.interview_id,
        event_data={"practice_type": "structure", "track": track},
    )
    
    return StructurePracticeSessionResponse(
        practice_id=practice.id,
        interview_id=practice.interview_id,
        track=practice.track,
        questions=practice.questions,
        status=practice.status,
        created_at=practice.created_at,
    )


@router.post(
    path="/structure-practice/{practice_id}/question/{question_index}/section/{section_name}/submit",
    name="structure-practice:submit-section",
    response_model=StructurePracticeAnswerSubmitResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Submit an audio answer for a specific section of a structure practice question",
)
async def submit_structure_practice_section(
    practice_id: int,
    question_index: int,
    section_name: str,
    file: UploadFile = File(..., description="Audio file with answer for this section. Supported: .mp3, .wav, .m4a, .flac (max 25MB)"),
    language: str = Form(default="en", description="Language code for transcription"),
    time_spent_seconds: int = Form(default=None, description="Time spent on this section"),
    current_user=fastapi.Depends(get_current_user),
    structure_practice_repo: StructurePracticeCRUDRepository = fastapi.Depends(get_repository(repo_type=StructurePracticeCRUDRepository)),
    answer_repo: StructurePracticeAnswerCRUDRepository = fastapi.Depends(get_repository(repo_type=StructurePracticeAnswerCRUDRepository)),
) -> StructurePracticeAnswerSubmitResponse:
    """
    Submit an audio answer for a specific section of a question in a structure practice session.
    Audio is transcribed using Whisper API. Returns hint for the next section.
    """
    from src.services.progressive_hints import get_next_section_hint, get_completion_message
    
    # Validate practice session ownership
    practice = await structure_practice_repo.get_by_id_and_user(
        practice_id=practice_id,
        user_id=current_user.id,
    )
    
    if not practice:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Structure practice session not found",
        )
    
    # Validate question index
    if question_index < 0 or question_index >= len(practice.questions):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail=f"question_index {question_index} out of range for this practice session",
        )
    
    # Get question and validate section
    question = practice.questions[question_index]
    framework = question.get("framework", "C-T-E-T-D")
    sections = question.get("sections", [])
    
    if section_name not in sections:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid section_name '{section_name}' for framework {framework}. Valid sections: {sections}",
        )
    
    # Validate and process audio file
    try:
        audio_bytes, file_metadata = await validate_audio_file(file)
    except fastapi.HTTPException:
        raise
    except Exception as e:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Audio file validation failed: {str(e)}"
        )
    
    # Validate language
    validated_language = validate_transcription_language(language)
    
    # Transcribe with Whisper API
    transcription, whisper_error, whisper_latency_ms, whisper_model = await transcribe_audio_with_whisper(
        audio_bytes=audio_bytes,
        filename=file_metadata["filename"],
        language=validated_language
    )
    
    if whisper_error or not transcription:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Transcription failed: {whisper_error or 'Unknown error'}"
        )
    
    # Extract transcription text
    transcription_text = transcription.get("text", "")
    if not transcription_text or len(transcription_text.strip()) < 5:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail="Transcribed answer is too short. Please provide a more detailed answer."
        )
    
    # Save audio file (optional, for record keeping)
    temp_file_path = ""
    try:
        temp_file_path, audio_url = await save_audio_file(
            audio_bytes=audio_bytes,
            filename=file_metadata["filename"],
            user_id=current_user.id,
            question_attempt_id=0  # Structure practice doesn't use question attempts
        )
    except Exception as e:
        logger.warning(f"Failed to save audio file: {e}")
    finally:
        if temp_file_path:
            await cleanup_temp_audio_file(temp_file_path)
    
    # Create answer for this section
    answer = await answer_repo.create_answer(
        practice_id=practice_id,
        question_index=question_index,
        section_name=section_name,
        answer_text=transcription_text,
        time_spent_seconds=time_spent_seconds,
    )
    
    # Get all submitted sections for this question
    all_answers = await answer_repo.list_by_practice_and_question(
        practice_id=practice_id,
        question_index=question_index,
    )
    
    # Count completed sections
    completed_sections = {a.section_name for a in all_answers}
    sections_complete = len(completed_sections)
    total_sections = len(sections)
    
    # Get next section hint
    next_info = get_next_section_hint(framework, section_name, transcription_text)
    
    if next_info is None:
        # All sections complete
        return StructurePracticeAnswerSubmitResponse(
            answer_id=answer.id,
            practice_id=practice_id,
            question_index=question_index,
            section_name=section_name,
            sections_complete=sections_complete,
            total_sections=total_sections,
            next_section=None,
            next_section_hint=None,
            is_complete=True,
            message=get_completion_message(framework),
        )
    else:
        # More sections to go
        return StructurePracticeAnswerSubmitResponse(
            answer_id=answer.id,
            practice_id=practice_id,
            question_index=question_index,
            section_name=section_name,
            sections_complete=sections_complete,
            total_sections=total_sections,
            next_section=next_info["section_name"],
            next_section_hint=next_info["hint"],
            is_complete=False,
            message=f"Section '{section_name}' recorded successfully ({whisper_model}, {whisper_latency_ms}ms). Continue to {next_info['section_name']}.",
        )


@router.post(
    path="/structure-practice/{practice_id}/question/{question_index}/analyze",
    name="structure-practice:analyze-answer",
    response_model=StructurePracticeAnalysisResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Analyze a submitted structure practice answer",
)
async def analyze_structure_practice_answer(
    practice_id: int,
    question_index: int,
    current_user=fastapi.Depends(get_current_user),
    structure_practice_repo: StructurePracticeCRUDRepository = fastapi.Depends(get_repository(repo_type=StructurePracticeCRUDRepository)),
    answer_repo: StructurePracticeAnswerCRUDRepository = fastapi.Depends(get_repository(repo_type=StructurePracticeAnswerCRUDRepository)),
) -> StructurePracticeAnalysisResponse:
    """
    Analyze a submitted answer and return detailed framework breakdown.
    Returns the progress report with completion percentage, time per section, and insights.
    """
    # Validate practice session ownership
    practice = await structure_practice_repo.get_by_id_and_user(
        practice_id=practice_id,
        user_id=current_user.id,
    )
    
    if not practice:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Structure practice session not found",
        )
    
    # Validate question index
    if question_index < 0 or question_index >= len(practice.questions):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail=f"question_index {question_index} out of range",
        )
    
    # Get all section answers for this question
    section_answers = await answer_repo.list_by_practice_and_question(
        practice_id=practice_id,
        question_index=question_index,
    )
    
    if not section_answers:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="No sections submitted yet. Submit at least one section first.",
        )
    
    # Get question details
    question_data = practice.questions[question_index]
    question_text = question_data.get("text", "")
    structure_hint = question_data.get("structure_hint", "")
    framework = question_data.get("framework", "STAR")
    expected_sections = question_data.get("sections", [])
    
    # Combine all section answers into structured format
    # Build a map of section_name -> answer data
    sections_data = {
        ans.section_name: {
            "answer_text": ans.answer_text,
            "time_spent_seconds": ans.time_spent_seconds or 0,
            "submitted": True,
        }
        for ans in section_answers
    }
    
    # Combine all answer texts in section order
    combined_answer = "\n\n".join([
        f"[{section}]\n{sections_data[section]['answer_text']}"
        for section in expected_sections
        if section in sections_data
    ])
    
    # Analyze the answer using LLM
    analysis_result, error, latency_ms, llm_model = await analyze_structure_answer(
        question_text=question_text,
        structure_hint=structure_hint,
        answer_text=combined_answer,
        framework=framework,
        submitted_sections=sections_data,
        expected_sections=expected_sections,
    )
    
    if error or not analysis_result:
        logger.error(f"Structure analysis error: {error}")
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to analyze answer: {error or 'Unknown error'}",
        )
    
    # Build framework progress using actual section data
    sections = [
        FrameworkSection(
            name=section.name,
            status="complete" if section.present and section.quality == "good" else (
                "partial" if section.present else "missing"
            ),
            answer_recorded=section.present,
            time_spent_seconds=sections_data.get(section.name, {}).get("time_spent_seconds", section.time_estimate_seconds),
        )
        for section in analysis_result.sections
    ]
    
    framework_progress = FrameworkProgress(
        framework_name=analysis_result.framework_detected,
        sections=sections,
        completion_percentage=analysis_result.completion_percentage,
        sections_complete=len(sections_data),  # Count actually submitted sections
        total_sections=len(expected_sections),
        progress_message=analysis_result.progress_message,
    )
    
    # Build time per section using actual recorded times
    time_per_section = [
        TimePerSection(
            section_name=section.name,
            seconds=sections_data.get(section.name, {}).get("time_spent_seconds", 0),
        )
        for section in analysis_result.sections
    ]
    
    # Store analysis result in database
    import datetime
    analysis_data = {
        "framework_progress": framework_progress.model_dump(),
        "time_per_section": [t.model_dump() for t in time_per_section],
        "key_insight": analysis_result.key_insight,
    }
    
    # Store analysis on the most recent section answer
    latest_answer = section_answers[-1]  # Last submitted section
    await answer_repo.update_analysis(
        answer_id=latest_answer.id,
        analysis_result=analysis_data,
    )

    await track_analytics_event(
        answer_repo.async_session,
        event_type="practice_completed",
        user_id=current_user.id,
        interview_id=practice.interview_id,
        event_data={
            "practice_type": "structure",
            "practice_id": practice_id,
            "question_index": question_index,
            "completion_percentage": analysis_result.completion_percentage,
        },
    )
    
    return StructurePracticeAnalysisResponse(
        answer_id=latest_answer.id,
        practice_id=practice_id,
        question_index=question_index,
        framework_progress=framework_progress,
        time_per_section=time_per_section,
        key_insight=analysis_result.key_insight,
        analyzed_at=datetime.datetime.utcnow(),
        llm_model=llm_model,
        llm_latency_ms=latency_ms,
    )
