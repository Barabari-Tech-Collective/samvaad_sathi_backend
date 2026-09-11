"""Analysis routes for aggregating multiple analysis types."""

import fastapi
import pydantic
import logging
import time
import random
from fastapi.security import HTTPAuthorizationCredentials
from typing import Any, Dict, List, Optional

from src.api.dependencies.auth import get_current_user, security
from src.api.dependencies.repository import get_repository
from src.api.dependencies.session import get_async_session
from src.models.schemas.base import BaseSchemaModel
from src.models.schemas.analysis import (
    CompleteAnalysisRequest,
    CompleteAnalysisResponse,
    DomainAnalysisRequest,
    CommunicationAnalysisRequest,
    DomainAnalysisResponse,
    CommunicationAnalysisResponse,
    PaceAnalysisResponse,
    PauseAnalysisResponse,
    PaceAnalysisRequest,
    PauseAnalysisRequest,
)
from src.repository.crud.question import QuestionAttemptCRUDRepository
from src.models.db.user import User
from src.services.analysis import analysis_service
from src.services.pace_analysis import provide_pace_feedback
from src.services.pause_analysis import analyze_pauses_async
from src.services.llm import analyze_domain_with_llm, analyze_communication_with_llm, MIN_ANSWER_WORD_COUNT
from sqlalchemy.ext.asyncio import AsyncSession
logger = logging.getLogger(__name__)

router = fastapi.APIRouter(prefix="", tags=["analysis"])


@router.post(
    path="/complete-analysis",
    name="analysis:complete-analysis",
    response_model=CompleteAnalysisResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Aggregate multiple analysis types for a question attempt",
    description=(
        "Aggregates domain, communication, pace, and pause analyses for a question attempt. "
        "Requires authentication and user must own the question attempt. "
        "Results are saved to the question_attempt.analysis_json field."
    ),
)
async def complete_analysis(
    request: CompleteAnalysisRequest,
    credentials: HTTPAuthorizationCredentials = fastapi.Depends(security),
    current_user: User = fastapi.Depends(get_current_user),
    question_repo: QuestionAttemptCRUDRepository = fastapi.Depends(get_repository(repo_type=QuestionAttemptCRUDRepository)),
    db: AsyncSession = fastapi.Depends(get_async_session)
) -> CompleteAnalysisResponse:
    start_time = time.perf_counter()
    """
    Main endpoint for complete analysis workflow:
    1. Validate user owns the question attempt
    2. Verify transcription data exists
    3. Call individual analysis endpoints concurrently
    4. Aggregate results into single response
    5. Save to database
    6. Return comprehensive analysis with metadata
    """
    question_attempt_id = request.question_attempt_id
    user_id = current_user.id

    logger.info(
        "COMPLETE_ANALYSIS START | Attempt=%s |User=%s | AnalysisTypes=%s",
        question_attempt_id,
        user_id,
        request.analysis_types,
    )
    # Extract token for service calls
    auth_token = credentials.credentials
    
    try:
        aggregation_start = time.perf_counter()

        logger.info(
            "COMPLETE_ANALYSIS | Aggregation started for Attempt=%s | User=%s | Types=%s | StartTime=%s",
            question_attempt_id,
            user_id,
            request.analysis_types,
            aggregation_start,
        )
        # Perform aggregated analysis
        aggregated_analysis, metadata, saved, save_error = await analysis_service.aggregate_question_analysis(
            question_attempt_id=request.question_attempt_id,
            user_id=current_user.id,
            analysis_types=request.analysis_types,
            auth_token=auth_token,
            db=db
        )
        
        aggregation_time = time.perf_counter() - aggregation_start

        logger.info(
            "COMPLETE_ANALYSIS aggregation completed | "
            "Attempt=%s | Time=%.2fs | Saved=%s",
            question_attempt_id,
            aggregation_time,
            saved,
        )

        logger.info(
            "COMPLETE_ANALYSIS metadata | "
            "Attempt=%s | Completed=%s | Failed=%s",
            question_attempt_id,
            getattr(metadata, "completed_analyses", None),
            getattr(metadata, "failed_analyses", None),
        )
        # Determine completion status
        analysis_complete = len(metadata.failed_analyses) == 0

        logger.info(
            "COMPLETE_ANALYSIS status | "
            "Attempt=%s | Complete=%s | Completed=%s | Failed=%s",
            question_attempt_id,
            analysis_complete,
            metadata.completed_analyses,
            metadata.failed_analyses,
        )

        # Build response message
        if analysis_complete:
            if saved:
                message = "All analyses completed successfully and saved to database"
            else:
                message = f"All analyses completed but database save failed: {save_error}"
        else:
            if len(metadata.completed_analyses) > 0:
                message = f"Partial analysis completed. Failed: {', '.join(metadata.failed_analyses)}"
            else:
                message = "All analyses failed"

        logger.info(
            "COMPLETE_ANALYSIS message generated | Attempt=%s | Message=%s",
            question_attempt_id,
            message,
        )
        
        response_payload = {
            "question_attempt_id": question_attempt_id,
            "analysis_complete": analysis_complete,
            "aggregated_analysis": aggregated_analysis,
            "metadata": metadata,
            "saved": saved,
            "save_error": save_error,
            "message": message,
        }

        logger.info(
            "COMPLETE_ANALYSIS validating response | Attempt=%s",
            question_attempt_id,
        )

        try:
            response = CompleteAnalysisResponse(**response_payload)

        except pydantic.ValidationError as validation_error:

            logger.error(
                "COMPLETE_ANALYSIS PYDANTIC VALIDATION FAILED | "
                "Attempt=%s | Errors=%s",
                question_attempt_id,
                validation_error.errors(),
                exc_info=True,
            )

            # Log only structure/types, not potentially sensitive transcript data
            logger.error(
                "COMPLETE_ANALYSIS invalid response structure | "
                "Attempt=%s | Keys=%s | aggregated_type=%s | metadata_type=%s",
                question_attempt_id,
                list(response_payload.keys()),
                type(aggregated_analysis).__name__,
                type(metadata).__name__,
            )

            raise fastapi.HTTPException(
                status_code=500,
                detail="Complete analysis response validation failed",
            )      

        total_time = time.perf_counter() - start_time

        logger.info(
            "COMPLETE_ANALYSIS SUCCESS | "
            "Attempt=%s | TotalTime=%.2fs | Complete=%s | Saved=%s",
            question_attempt_id,
            total_time,
            analysis_complete,
            saved,
        )

        return response

    except fastapi.HTTPException:
        raise

    except ValueError as e:

        logger.error(
            "COMPLETE_ANALYSIS VALUE ERROR | Attempt=%s | Error=%s",
            question_attempt_id,
            str(e),
            exc_info=True,
        )

        if "not found" in str(e).lower():
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_404_NOT_FOUND,
                detail=str(e),
            )

        elif "access denied" in str(e).lower():
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_403_FORBIDDEN,
                detail=str(e),
            )

        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    except Exception as e:

        logger.exception(
            "COMPLETE_ANALYSIS UNEXPECTED ERROR | "
            "Attempt=%s | ErrorType=%s | Error=%s",
            question_attempt_id,
            type(e).__name__,
            str(e),
        )

        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis aggregation failed: {str(e)}",
        )


    #     return CompleteAnalysisResponse(
    #         question_attempt_id=request.question_attempt_id,
    #         analysis_complete=analysis_complete,
    #         aggregated_analysis=aggregated_analysis,
    #         metadata=metadata,
    #         saved=saved,
    #         save_error=save_error,
    #         message=message
    #     )
        
    # except ValueError as e:
    #     # Handle validation errors (missing question attempt, no transcription, etc.)
    #     if "not found" in str(e).lower():
    #         raise fastapi.HTTPException(
    #             status_code=fastapi.status.HTTP_404_NOT_FOUND,
    #             detail=str(e)
    #         )
    #     elif "access denied" in str(e).lower():
    #         raise fastapi.HTTPException(
    #             status_code=fastapi.status.HTTP_403_FORBIDDEN,
    #             detail=str(e)
    #         )
    #     else:
    #         raise fastapi.HTTPException(
    #             status_code=fastapi.status.HTTP_400_BAD_REQUEST,
    #             detail=str(e)
    #         )
    
    # except Exception as e:
    #     # Handle unexpected errors
    #     raise fastapi.HTTPException(
    #         status_code=fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR,
    #         detail=f"Analysis aggregation failed: {str(e)}"
    #     )


# Domain analysis endpoint (LLM-backed)
@router.post(
    path="/domain-base-analysis",
    name="analysis:domain-base",
    response_model=DomainAnalysisResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Analyze domain knowledge from question attempt",
    description=(
        "Evaluates the domain-specific knowledge demonstrated in a question attempt answer using an LLM. "
        "Persists results under analysis_json.domain."
    ),
)
async def domain_base_analysis(
    request: DomainAnalysisRequest,
    current_user: User = fastapi.Depends(get_current_user),
    question_repo: QuestionAttemptCRUDRepository = fastapi.Depends(get_repository(repo_type=QuestionAttemptCRUDRepository)),
) -> DomainAnalysisResponse:
    try:
        qa = await question_repo.get_by_id_and_user(
            question_attempt_id=request.question_attempt_id,
            user_id=current_user.id,
        )
        if not qa:
            raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Question attempt not found")

        # Choose transcription text
        transcription_text: str | None = None
        if request.override_transcription:
            transcription_text = request.override_transcription.strip()
        elif qa.transcription:
            transcription_text = qa.transcription.get("text") or qa.transcription.get("transcript")
        if not transcription_text:
            raise fastapi.HTTPException(status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Transcription missing")

        # Build user profile context
        years = getattr(current_user, "years_experience", None)
        skills_json = getattr(current_user, "skills", None) or {}
        skills_list = skills_json.get("items") if isinstance(skills_json, dict) else None
        profile = {
            "years_experience": years,
            "skills": (skills_list or [])[:30],
            "job_role": request.job_role,
            "track": None,
        }

        analysis, llm_error, latency_ms, llm_model = await analyze_domain_with_llm(
            user_profile=profile,
            question_text=getattr(qa, "question_text", None),
            transcription=transcription_text,
        )

        if not analysis:
            analysis = {
                "overall_score": None,
                "summary": "",
                "suggestions": [],
                "confidence": 0.0,
                "llm_error": llm_error,
            }

        score = analysis.get("overall_score") if isinstance(analysis.get("overall_score"), (int, float)) else 0.0
        feedback = analysis.get("summary") or analysis.get("domain_feedback") or ""
        knowledge_areas = analysis.get("knowledge_areas") or []
        if not knowledge_areas and isinstance(analysis.get("criteria"), dict):
            knowledge_areas = [k for k in (analysis["criteria"].keys())]
        strengths = analysis.get("strengths") or []
        improvements = analysis.get("improvements") or analysis.get("suggestions") or []

        merged = dict(qa.analysis_json or {})
        normalized = {
            "domain_score": float(score or 0.0),
            "domain_feedback": str(feedback),
            "knowledge_areas": [str(x) for x in knowledge_areas][:10],
            "strengths": [str(x) for x in strengths][:10],
            "improvements": [str(x) for x in improvements][:10],
            "llm_model": llm_model,
            "llm_latency_ms": latency_ms,
        }
        merged["domain"] = normalized
        try:
            await question_repo.update_analysis_json(question_attempt_id=request.question_attempt_id, analysis_json=merged)
        except Exception:
            pass

        return DomainAnalysisResponse(
            question_attempt_id=request.question_attempt_id,
            domain_score=normalized["domain_score"],
            domain_feedback=normalized["domain_feedback"],
            knowledge_areas=normalized["knowledge_areas"],
            strengths=normalized["strengths"],
            improvements=normalized["improvements"],
        )
    except fastapi.HTTPException:
        raise
    except Exception as e:
        # Restore 500 behavior for actual LLM/debugging visibility
        raise fastapi.HTTPException(status_code=500, detail=f"Domain analysis failed: {e}")


@router.post(
    path="/communication-based-analysis",
    name="analysis:communication-base",  
    response_model=CommunicationAnalysisResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Analyze communication quality from question attempt",
    description=(
        "Evaluates clarity, grammar, vocabulary, and structure using an LLM. "
        "Persists results under analysis_json.communication."
    ),
)
async def communication_based_analysis(
    request: CommunicationAnalysisRequest,
    current_user: User = fastapi.Depends(get_current_user),
    question_repo: QuestionAttemptCRUDRepository = fastapi.Depends(get_repository(repo_type=QuestionAttemptCRUDRepository)),
) -> CommunicationAnalysisResponse:
    try:
        qa = await question_repo.get_by_id_and_user(
            question_attempt_id=request.question_attempt_id,
            user_id=current_user.id,
        )
        if not qa:
            raise fastapi.HTTPException(status_code=fastapi.status.HTTP_404_NOT_FOUND, detail="Question attempt not found")

        transcription_text: str | None = None
        if request.override_transcription:
            transcription_text = request.override_transcription.strip()
        elif qa.transcription:
            transcription_text = qa.transcription.get("text") or qa.transcription.get("transcript")
        if not transcription_text:
            raise fastapi.HTTPException(status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Transcription missing")

        years = getattr(current_user, "years_experience", None)
        skills_json = getattr(current_user, "skills", None) or {}
        skills_list = skills_json.get("items") if isinstance(skills_json, dict) else None
        profile = {
            "years_experience": years,
            "skills": (skills_list or [])[:30],
            "job_role": request.job_role,
            "track": None,
        }
        prior_metrics = (qa.analysis_json or {})

        analysis, llm_error, latency_ms, llm_model = await analyze_communication_with_llm(
            user_profile=profile,
            question_text=getattr(qa, "question_text", None),
            transcription=transcription_text,
            aux_metrics={
                "pace": prior_metrics.get("pace"),
                "pause": prior_metrics.get("pause"),
            },
        )

        if not analysis:
            analysis = {
                "overall_score": None,
                "summary": "",
                "suggestions": [],
                "confidence": 0.0,
                "llm_error": llm_error,
            }

        def _num(value: Any, fallback: float) -> float:
            try:
                return float(value) if isinstance(value, (int, float)) else float(fallback)
            except Exception:
                return float(fallback)

        base = _num(analysis.get("overall_score"), 0.0)
        clarity = base
        vocab = base
        grammar = base
        structure = base
        crit = analysis.get("criteria")
        if isinstance(crit, dict):
            clarity_score = crit.get("clarity", {}).get("score") if isinstance(crit.get("clarity"), dict) else None
            clarity = _num(clarity_score, base)
            structure_score = crit.get("structure", {}).get("score") if isinstance(crit.get("structure"), dict) else None
            structure = _num(structure_score, base)
            vocab_score = crit.get("vocabulary", {}).get("score") if isinstance(crit.get("vocabulary"), dict) else None
            jargon_score = crit.get("jargon_use", {}).get("score") if isinstance(crit.get("jargon_use"), dict) else None
            vocab = _num(vocab_score if isinstance(vocab_score, (int, float)) else jargon_score, base)
            grammar = _num(crit.get("grammar", {}).get("score") if isinstance(crit.get("grammar"), dict) else None, base)

        feedback = analysis.get("summary") or analysis.get("communication_feedback") or ""
        
        # Extract strengths and improvements from LLM response
        strengths_raw = analysis.get("strengths") or []
        if not isinstance(strengths_raw, list):
            strengths_raw = [str(strengths_raw)]
        strengths = [str(x) for x in strengths_raw][:10]
        
        improvements_raw = analysis.get("improvements") or []
        if not isinstance(improvements_raw, list):
            improvements_raw = [str(improvements_raw)]
        improvements = [str(x) for x in improvements_raw][:10]
        
        # Keep recommendations for backward compatibility
        recs_raw = analysis.get("suggestions") or analysis.get("recommendations") or []
        if not isinstance(recs_raw, list):
            recs_raw = [str(recs_raw)]
        recommendations = [str(x) for x in recs_raw][:10]

        merged = dict(qa.analysis_json or {})
        normalized_comm = {
            "communication_score": base,
            "clarity_score": clarity,
            "vocabulary_score": vocab,
            "grammar_score": grammar,
            "structure_score": structure,
            "communication_feedback": str(feedback),
            "strengths": strengths,
            "improvements": improvements,
            "recommendations": recommendations,
            "llm_model": llm_model,
            "llm_latency_ms": latency_ms,
        }
        merged["communication"] = normalized_comm
        try:
            await question_repo.update_analysis_json(question_attempt_id=request.question_attempt_id, analysis_json=merged)
        except Exception:
            pass

        return CommunicationAnalysisResponse(
            question_attempt_id=request.question_attempt_id,
            communication_score=normalized_comm["communication_score"],
            clarity_score=normalized_comm["clarity_score"],
            vocabulary_score=normalized_comm["vocabulary_score"],
            grammar_score=normalized_comm["grammar_score"],
            structure_score=normalized_comm["structure_score"],
            communication_feedback=normalized_comm["communication_feedback"],
            strengths=normalized_comm["strengths"],
            improvements=normalized_comm["improvements"],
            recommendations=normalized_comm["recommendations"],
        )
    except fastapi.HTTPException:
        raise
    except Exception as e:
        raise fastapi.HTTPException(status_code=500, detail=f"Communication analysis failed: {e}")


# Backward-compatible alias for naming consistency


@router.post(
    path="/analyze-pace",
    name="analysis:analyze-pace",
    response_model=PaceAnalysisResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Analyze speaking pace from question attempt",
    description="Evaluates speaking speed and pace patterns from transcription timestamps."
)
async def analyze_pace(
    request: PaceAnalysisRequest,
    current_user = fastapi.Depends(get_current_user),
    question_repo: QuestionAttemptCRUDRepository = fastapi.Depends(get_repository(repo_type=QuestionAttemptCRUDRepository))
) -> PaceAnalysisResponse:
    """Stub implementation for pace analysis."""
    question_attempt_id = int(request.question_attempt_id)
    
    # Verify ownership
    qa = await question_repo.get_by_id_and_user(
        question_attempt_id=question_attempt_id,
        user_id=current_user.id
    )
    
    if not qa or not qa.transcription:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Question attempt not found or no transcription available"
        )
    trans = qa.transcription or {}
    words = trans.get("words")
    if (not isinstance(words, list) or len(words) == 0) and isinstance(trans.get("words_timestamp"), dict):
        words = (trans["words_timestamp"] or {}).get("words")
    if not isinstance(words, list) or len(words) == 0:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Transcription missing word-level timestamps"
        )

    # Word-level timestamps existing doesn't mean there was a real answer —
    # a single stray/hallucinated word still has "timestamps." Don't compute
    # a plausible-looking WPM off negligible speech.
    if len(words) < MIN_ANSWER_WORD_COUNT:
        feedback = "Not enough speech to analyze pacing — no substantial answer was given."
        pace_score = 0.0
        wpm = 0.0
        pace_category = "insufficient_speech"
    else:
        word_level_timestemps = {"words": words}
        res = provide_pace_feedback(word_level_timestemps)

        if not isinstance(res, dict):
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Insufficient data for pace analysis"
            )

        feedback = str(res.get("feedback", ""))
        raw_score = res.get("score")
        wpm = float(res.get("wpm") or 0.0)
        try:
            raw_score_f = float(raw_score) if raw_score is not None else 0.0
        except Exception:
            raw_score_f = 0.0
        # Normalize 0-5 -> 0-100 if needed
        pace_score = raw_score_f * 20.0 if raw_score_f <= 5.0 else raw_score_f

        # Determine pace category
        if wpm < 120:
            pace_category = "too_slow"
        elif wpm > 200:
            pace_category = "too_fast"
        else:
            pace_category = "optimal"
    
    if pace_category == "insufficient_speech":
        recommendations = ["Give a substantial spoken answer before pacing can be assessed."]
    elif pace_category == "optimal":
        recommendations = ["Maintain current pace", "Consider slight variation for emphasis"]
    else:
        recommendations = ["Adjust speaking speed", "Practice with metronome"]

    # Persist into analysis_json.pace (merge)
    merged = dict(qa.analysis_json or {})
    merged["pace"] = {
        "pace_score": pace_score,
        "words_per_minute": wpm,
        "pace_feedback": feedback,
        "pace_category": pace_category,
        "recommendations": recommendations,
    }
    await question_repo.update_analysis_json(
        question_attempt_id=question_attempt_id,
        analysis_json=merged,
    )

    return PaceAnalysisResponse(
        question_attempt_id=question_attempt_id,
        pace_score=pace_score,
        words_per_minute=wpm,
        pace_feedback=feedback,
        pace_category=pace_category,
        recommendations=recommendations,
    )


@router.post(
    path="/analyze-pause",
    name="analysis:analyze-pause",
    response_model=PauseAnalysisResponse,
    status_code=fastapi.status.HTTP_200_OK,
    summary="Analyze pause patterns from question attempt", 
    description="Evaluates pause frequency, duration, and patterns from transcription timestamps."
)
async def analyze_pause(
    request: PauseAnalysisRequest,
    current_user = fastapi.Depends(get_current_user),
    question_repo: QuestionAttemptCRUDRepository = fastapi.Depends(get_repository(repo_type=QuestionAttemptCRUDRepository))
) -> PauseAnalysisResponse:
    """Stub implementation for pause analysis."""
    question_attempt_id = int(request.question_attempt_id)
    
    # Verify ownership
    qa = await question_repo.get_by_id_and_user(
        question_attempt_id=question_attempt_id,
        user_id=current_user.id
    )
    
    if not qa or not qa.transcription:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="Question attempt not found or no transcription available"
        )
    
    trans = qa.transcription or {}
    words = trans.get("words")
    if (not isinstance(words, list) or len(words) == 0) and isinstance(trans.get("words_timestamp"), dict):
        words = (trans["words_timestamp"] or {}).get("words")
    if not isinstance(words, list) or len(words) == 0:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Transcription missing word-level timestamps"
        )

    # Word-level timestamps existing doesn't mean there was a real answer —
    # a single stray/hallucinated word still has "timestamps." Don't compute
    # a plausible-looking pause pattern off negligible speech.
    if len(words) < MIN_ANSWER_WORD_COUNT:
        res = {
            "overview": "Not enough speech to analyze pause patterns — no substantial answer was given.",
            "details": [],
            "distribution": {"long": "0%", "rushed": "0%", "strategic": "0%", "normal": "0%"},
            "actionable_feedback": "Give a substantial spoken answer before pause patterns can be assessed.",
            "score": 0,
        }
    else:
        word_level_timestemps = {"words": words}
        res = await analyze_pauses_async(word_level_timestemps)

        if not isinstance(res, dict):
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Insufficient data for pause analysis"
            )

    # Normalize score 1-5 -> 0-100 if needed
    raw_pause_score = res.get('score')
    try:
        raw_pause_score_f = float(raw_pause_score) if raw_pause_score is not None else 0.0
    except Exception:
        raw_pause_score_f = 0.0
    pause_score = raw_pause_score_f * 20.0 if raw_pause_score_f <= 5.0 else raw_pause_score_f

    # Persist into analysis_json.pause (merge)
    merged = dict(qa.analysis_json or {})
    merged["pause"] = {
        "pause_score": pause_score,
        "overview": res.get('overview', ''),
        "details": res.get('details', []),
        "distribution": res.get('distribution', {}),
        "actionable_feedback": res.get('actionable_feedback', ''),
    }
    await question_repo.update_analysis_json(
        question_attempt_id=question_attempt_id,
        analysis_json=merged,
    )

    return PauseAnalysisResponse(
        question_attempt_id=question_attempt_id,
        overview=res.get('overview', ''),
        details=res.get('details', []),
        distribution=res.get('distribution', {}),
        actionable_feedback=res.get('actionable_feedback', ''),
        pause_score=pause_score,
    )
