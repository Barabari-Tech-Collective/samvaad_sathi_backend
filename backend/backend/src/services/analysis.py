"""Analysis aggregation service for combining multiple analysis types."""

import asyncio
import json
import time
import random
import logging
import pydantic
from typing import Dict, List, Any, Tuple
import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config.manager import settings
from src.models.db.question_attempt import QuestionAttempt
from src.models.schemas.analysis import (
    AggregatedAnalysis, 
    AnalysisMetadata,
    DomainAnalysisResponse,
    CommunicationAnalysisResponse, 
    PaceAnalysisResponse,
    PauseAnalysisResponse
)

logger = logging.getLogger(__name__)
DEFAULT_ANALYSIS_TYPES = ("domain", "communication", "pace", "pause")


def _has_transcribed_answer(question_attempt: QuestionAttempt) -> bool:
    transcription = question_attempt.transcription
    if not transcription:
        return False
    if isinstance(transcription, dict):
        text = transcription.get("text") or transcription.get("transcript")
        return bool(str(text or "").strip())
    return bool(str(transcription).strip())


def _missing_analysis_types(question_attempt: QuestionAttempt) -> list[str]:
    analysis = question_attempt.analysis_json if isinstance(question_attempt.analysis_json, dict) else {}
    return [
        analysis_type
        for analysis_type in DEFAULT_ANALYSIS_TYPES
        if not isinstance(analysis.get(analysis_type), dict) or not analysis.get(analysis_type)
    ]


class AnalysisAggregationService:
    """Service for aggregating multiple analysis types into a single result."""
    
    def __init__(self, base_url: str = None):
        self.base_url = base_url or "http://127.0.0.1:8000/api"
        self.timeout = 90.0  # 90 seconds per analysis
        
    async def aggregate_question_analysis(
        self,
        question_attempt_id: int,
        user_id: int,
        analysis_types: List[str],
        auth_token: str,
        db: AsyncSession
    ) -> Tuple[AggregatedAnalysis, AnalysisMetadata, bool, str | None]:
        """
        Aggregate multiple analysis types for a question attempt.
        
        Args:
            question_attempt_id: ID of the question attempt to analyze
            user_id: ID of the user making the request
            analysis_types: List of analysis types to perform
            auth_token: Bearer token for authentication
            db: Database session
            
        Returns:
            Tuple of (aggregated_analysis, metadata, saved_successfully, save_error)
        """
        start_time = time.perf_counter()


        logger.info(
          "COMPLETE_ANALYSIS START | Attempt=%s | User=%s | AnalysisTypes=%s",
           question_attempt_id,
           user_id,
           analysis_types,
        )

       # Verify question attempt exists and has transcription
        logger.info(
          "COMPLETE_ANALYSIS verifying question attempt | Attempt=%s | User=%s",
           question_attempt_id,
           user_id,
        )
        
        # Verify question attempt exists and has transcription
        question_attempt = await self._verify_question_attempt(
            question_attempt_id, user_id, db
        )
        
        if not question_attempt:
            logger.error(
              "COMPLETE_ANALYSIS question attempt NOT FOUND/ACCESS DENIED | Attempt=%s | User=%s",
              question_attempt_id,
              user_id,
            )
            raise ValueError("Question attempt not found or access denied")
            
        logger.info(
          "COMPLETE_ANALYSIS question attempt verified | Attempt=%s | HasTranscription=%s | HasAnalysisJSON=%s",
          question_attempt_id,
          bool(question_attempt.transcription),
          bool(question_attempt.analysis_json),
        )
        if not question_attempt.transcription:
            logger.error(
              "COMPLETE_ANALYSIS transcription missing | Attempt=%s",
              question_attempt_id,
            )
            raise ValueError("Question attempt does not have transcription data")
        logger.info(
          "COMPLETE_ANALYSIS starting concurrent analyses | Attempt=%s | Types=%s",
          question_attempt_id,
          analysis_types,
        )
        # Run analyses concurrently
        analysis_results = await self._run_concurrent_analyses(
            question_attempt_id, analysis_types, auth_token, question_attempt, user_id
        )

        logger.info(
        "COMPLETE_ANALYSIS building aggregated response | Attempt=%s",
        question_attempt_id,
        )
        
        # Aggregate results
        aggregated_analysis = self._build_aggregated_analysis(analysis_results)
        
        # Calculate metadata
        end_time = time.perf_counter()
        total_latency_ms = int((end_time - start_time) * 1000)
        
        completed_analyses = [
            analysis_type for analysis_type, result in analysis_results.items()
            if result.get("success", False)
        ]
        failed_analyses = [
            analysis_type for analysis_type, result in analysis_results.items()
            if not result.get("success", False)
        ]
        
        metadata = AnalysisMetadata(
            total_latency_ms=total_latency_ms,
            completed_analyses=completed_analyses,
            failed_analyses=failed_analyses,
            partial_failure=len(failed_analyses) > 0 and len(completed_analyses) > 0
        )

        logger.info(
          "COMPLETE_ANALYSIS metadata created | Attempt=%s | Completed=%s | Failed=%s | Latency=%sms",
          question_attempt_id,
          completed_analyses,
          failed_analyses,
          total_latency_ms,
        )

        logger.info(
          "COMPLETE_ANALYSIS saving results to DB | Attempt=%s",
          question_attempt_id,
        )
        # Save to database (deep-merge to avoid losing other keys)
        saved, save_error = await self._save_analysis_to_db(
            question_attempt, aggregated_analysis, db
        )

        logger.info(
          "COMPLETE_ANALYSIS DB save completed | Attempt=%s | Saved=%s | Error=%s",
          question_attempt_id,
          saved,
          save_error,
        )
        
        return aggregated_analysis, metadata, saved, save_error

    async def ensure_recorded_attempts_analyzed(
        self,
        *,
        question_attempts: list[QuestionAttempt],
        user_id: int,
        db: AsyncSession,
        logger: Any = None,
    ) -> int:
        """Analyze recorded attempts that are missing report-critical analysis sections."""
        analyzed_count = 0

        for question_attempt in question_attempts:
            if not _has_transcribed_answer(question_attempt):
                continue

            missing_analysis_types = _missing_analysis_types(question_attempt)
            if not missing_analysis_types:
                continue

            try:
                _, metadata, saved, save_error = await self.aggregate_question_analysis(
                    question_attempt_id=question_attempt.id,
                    user_id=user_id,
                    analysis_types=missing_analysis_types,
                    auth_token="",
                    db=db,
                )
                analyzed_count += 1

                if logger and (metadata.failed_analyses or not saved):
                    logger.warning(
                        "Pre-report analysis incomplete for question_attempt_id=%s missing_types=%s failed_types=%s saved=%s save_error=%s",
                        question_attempt.id,
                        missing_analysis_types,
                        metadata.failed_analyses,
                        saved,
                        save_error,
                    )
            except Exception as exc:  # noqa: BLE001
                if logger:
                    logger.warning(
                        "Pre-report analysis failed for question_attempt_id=%s missing_types=%s: %s",
                        question_attempt.id,
                        missing_analysis_types,
                        exc,
                    )

        return analyzed_count
    
    async def _verify_question_attempt(
        self, 
        question_attempt_id: int, 
        user_id: int, 
        db: AsyncSession
    ) -> QuestionAttempt | None:
        """Verify question attempt exists and belongs to user."""
        stmt = (
            sqlalchemy.select(QuestionAttempt)
            .join(QuestionAttempt.interview)
            .options(selectinload(QuestionAttempt.question))
            .where(
                QuestionAttempt.id == question_attempt_id,
                QuestionAttempt.interview.has(user_id=user_id)
            )
        )
        query = await db.execute(statement=stmt)
        return query.scalar_one_or_none()
    
    async def _run_concurrent_analyses(
        self, 
        question_attempt_id: int, 
        analysis_types: List[str], 
        auth_token: str,
        question_attempt: QuestionAttempt,
        user_id: int
    ) -> Dict[str, Dict[str, Any]]:
        """Run multiple analyses concurrently with per-analysis timeouts."""
        logger.info(
          "ANALYSIS CONCURRENT START | Attempt=%s | Types=%s",
          question_attempt_id,
          analysis_types,
        )
        # Define supported analysis types
        SUPPORTED_ANALYSIS_TYPES = {"domain", "communication", "pace", "pause"}
        
        analysis_results: Dict[str, Dict[str, Any]] = {}
        
        tasks: list[tuple[str, asyncio.Task]] = []
        for analysis_type in analysis_types:
            if analysis_type not in SUPPORTED_ANALYSIS_TYPES:
                logger.error(
                  "ANALYSIS UNSUPPORTED TYPE | Attempt=%s | Type=%s",
                  question_attempt_id,
                  analysis_type,
                )
                analysis_results[analysis_type] = {
                    "success": False,
                    "error": f"Unsupported analysis type: {analysis_type}. Supported: {SUPPORTED_ANALYSIS_TYPES}",
                    "data": None,
                }
                continue
            task = asyncio.create_task(
                asyncio.wait_for(
                    self._generate_analysis_result(analysis_type, question_attempt_id, question_attempt, user_id),
                    timeout=self.timeout,
                )
            )
            tasks.append((analysis_type, task))
            logger.info(
              "ANALYSIS TASK CREATED | Attempt=%s | Type=%s",
              question_attempt_id,
              analysis_type,
            )
        for analysis_type, task in tasks:
            task_start = time.perf_counter()
            try:
                logger.info(
                  "ANALYSIS TASK WAITING | Attempt=%s | Type=%s",
                  question_attempt_id,
                  analysis_type,
                )
                result = await task
                task_latency = int((time.perf_counter() - task_start) * 1000)
                analysis_results[analysis_type] = result

                if result.get("success"):
                   logger.info(
                    "ANALYSIS TASK SUCCESS | Attempt=%s | Type=%s | Latency=%sms",
                    question_attempt_id,
                    analysis_type,
                    task_latency,
                   )
                else:
                   logger.error(
                    "ANALYSIS TASK FAILED | Attempt=%s | Type=%s | Latency=%sms | Error=%s",
                    question_attempt_id,
                    analysis_type,
                    task_latency,
                    result.get("error"),
                )


            except asyncio.TimeoutError:
                logger.exception(
                  "ANALYSIS TASK TIMEOUT | Attempt=%s | Type=%s | Timeout=%ss",
                  question_attempt_id,
                  analysis_type,
                  self.timeout,
                )
                analysis_results[analysis_type] = {
                    "success": False,
                    "error": f"Analysis timeout after {self.timeout}s",
                    "data": None,
                }
            except Exception as e:
                logger.exception(
                  "ANALYSIS TASK EXCEPTION | Attempt=%s | Type=%s | Error=%s",
                  question_attempt_id,
                  analysis_type,
                  str(e),
                )
                analysis_results[analysis_type] = {
                    "success": False,
                    "error": str(e),
                    "data": None,
                }
        
        return analysis_results
    
    async def _generate_analysis_result(
        self, 
        analysis_type: str, 
        question_attempt_id: int,
        question_attempt: QuestionAttempt,
        user_id: int
    ) -> Dict[str, Any]:
        """Generate analysis result for a specific type using real analysis services."""
        logger.info(
          "ANALYSIS GENERATION START | Attempt=%s | Type=%s | User=%s",
          question_attempt_id,
          analysis_type,
          user_id,
        )
        try:
            # Import the real analysis services
            from src.services.llm import analyze_domain_with_llm, analyze_communication_with_llm
            from src.services.pace_analysis import provide_pace_feedback
            from src.services.pause_analysis import analyze_pauses_async
            
            # Get transcription data
            transcription_text = None
            if question_attempt.transcription:
                transcription_text = question_attempt.transcription.get("text") or question_attempt.transcription.get("transcript")
            logger.info(
              "ANALYSIS TRANSCRIPTION CHECK | Attempt=%s | Type=%s | Exists=%s | Length=%s",
              question_attempt_id,
              analysis_type,
              bool(transcription_text),
              len(transcription_text or ""),
            )
            if not transcription_text:
                raise ValueError(f"No transcription available for {analysis_type} analysis")
            
            if analysis_type == "domain":
                # Build user profile for LLM analysis
                profile = {
                    "years_experience": None,  # Could be enhanced to get from user
                    "skills": [],
                    "job_role": None,
                    "track": None,
                }
                logger.info(
                  "DOMAIN LLM START | Attempt=%s",
                  question_attempt_id,
                )
                expected_answer = (
                    getattr(question_attempt.question, "expected_answer", None)
                    if question_attempt.question else None
                )
                # Call real LLM domain analysis
                analysis, llm_error, latency_ms, llm_model = await analyze_domain_with_llm(
                    user_profile=profile,
                    question_text=getattr(question_attempt, "question_text", None),
                    transcription=transcription_text,
                    expected_answer=expected_answer,
                )

                logger.info(
                  "DOMAIN LLM COMPLETE | Attempt=%s | Model=%s | Latency=%sms | Error=%s | ResponseType=%s | Keys=%s",
                  question_attempt_id,
                  llm_model,
                  latency_ms,
                  llm_error,
                  type(analysis).__name__,
                  list(analysis.keys()) if isinstance(analysis, dict) else None,
                )
                
                if not analysis:
                    analysis = {
                        "overall_score": 0.0,
                        "summary": "Unable to analyze domain knowledge - LLM analysis failed",
                        "suggestions": [],
                        "confidence": 0.0,
                        "llm_error": llm_error,
                    }
                
                # Map to expected response format
                score = analysis.get("overall_score", 0.0) if isinstance(analysis.get("overall_score"), (int, float)) else 0.0
                feedback = analysis.get("summary") or analysis.get("domain_feedback") or "Domain analysis completed"
                knowledge_areas = analysis.get("knowledge_areas") or []
                if not knowledge_areas and isinstance(analysis.get("criteria"), dict):
                    knowledge_areas = list(analysis["criteria"].keys())
                strengths = analysis.get("strengths") or []
                improvements = analysis.get("improvements") or analysis.get("suggestions") or []


                # Preserve the full analysis structure including criteria breakdown
                data = {
                    "question_attempt_id": question_attempt_id,
                    "domain_score": float(score),
                    "domain_feedback": str(feedback),
                    "knowledge_areas": [str(x) for x in knowledge_areas][:10],
                    "strengths": [str(x) for x in strengths][:10],
                    "improvements": [str(x) for x in improvements][:10],
                    # Preserve the full analysis structure for summary report processing
                    "overall_score": score,
                    "criteria": analysis.get("criteria", {}),
                    "summary": feedback,
                    "suggestions": improvements,
                    "confidence": analysis.get("confidence", 0.0)
                }
                logger.info(
                  "DOMAIN NORMALIZED DATA | Attempt=%s | DataKeys=%s | Score=%s | CriteriaType=%s",
                  question_attempt_id,
                  list(data.keys()),
                  data.get("overall_score"),
                  type(data.get("criteria")).__name__,
                )
                
            elif analysis_type == "communication":
                # Build user profile for LLM analysis
                profile = {
                    "years_experience": None,
                    "skills": [],
                    "job_role": None,
                    "track": None,
                }
                logger.info(
                  "COMMUNICATION LLM START | Attempt=%s",
                   question_attempt_id,
                )
                # Call real LLM communication analysis
                analysis, llm_error, latency_ms, llm_model = await analyze_communication_with_llm(
                    user_profile=profile,
                    question_text=getattr(question_attempt, "question_text", None),
                    transcription=transcription_text,
                    aux_metrics={},
                )

                logger.info(
                  "COMMUNICATION LLM COMPLETE | Attempt=%s | Model=%s | Latency=%sms | Error=%s | ResponseType=%s | Keys=%s",
                  question_attempt_id,
                  llm_model,
                  latency_ms,
                  llm_error,
                  type(analysis).__name__,
                  list(analysis.keys()) if isinstance(analysis, dict) else None,
                )
                
                if not analysis:
                    analysis = {
                        "overall_score": 0.0,
                        "summary": "Unable to analyze communication - LLM analysis failed",
                        "suggestions": [],
                        "confidence": 0.0,
                        "llm_error": llm_error,
                    }
                
                # Helper function to safely extract numeric values
                def _num(value: Any, fallback: float) -> float:
                    try:
                        return float(value) if isinstance(value, (int, float)) else float(fallback)
                    except Exception:
                        return float(fallback)
                
                # Map to expected response format
                base_score = _num(analysis.get("overall_score"), 0.0)
                feedback = analysis.get("summary") or "Communication analysis completed"
                recommendations = analysis.get("suggestions") or []
                # Preserve the full analysis structure including criteria breakdown
                data = {
                    "question_attempt_id": question_attempt_id,
                    "communication_score": base_score,
                    "clarity_score": base_score,  # Could be enhanced to get specific scores from LLM
                    "vocabulary_score": base_score,
                    "grammar_score": base_score,
                    "structure_score": base_score,
                    "communication_feedback": str(feedback),
                    "recommendations": [str(x) for x in recommendations][:10],
                    # Preserve the full analysis structure for summary report processing
                    "overall_score": base_score,
                    "criteria": analysis.get("criteria", {}),
                    "summary": feedback,
                    "suggestions": recommendations,
                    "confidence": analysis.get("confidence", 0.0)
                }
                
                logger.info(
                  "COMMUNICATION NORMALIZED DATA | Attempt=%s | DataKeys=%s | Score=%s | CriteriaType=%s",
                  question_attempt_id,
                  list(data.keys()),
                  data.get("overall_score"),
                  type(data.get("criteria")).__name__,
                )
            elif analysis_type == "pace":
                # Get word-level timestamps for pace analysis
                words_data = question_attempt.transcription.get("words", [])
                if not words_data:
                    raise ValueError("No word-level timestamps available for pace analysis")
                
                logger.info(
                  "PACE ANALYSIS START | Attempt=%s | WordCount=%s",
                  question_attempt_id,
                  len(words_data),
                )
                # Call real pace analysis
                pace_result = provide_pace_feedback({"words": words_data})
                
                logger.info(
                   "PACE ANALYSIS RESULT | Attempt=%s | ResultType=%s | ResultKeys=%s | Score=%s | WPM=%s",
                   question_attempt_id,
                   type(pace_result).__name__,
                   list(pace_result.keys()) if isinstance(pace_result, dict) else None,
                   pace_result.get("score") if isinstance(pace_result, dict) else None,
                   pace_result.get("wpm") if isinstance(pace_result, dict) else None,
                )


                if not pace_result:
                    raise ValueError("Pace analysis failed to process word timestamps")
                
                feedback = pace_result.get("feedback", "Pace analysis completed")
                raw_score = pace_result.get("score", 0.0)
                # Normalize 0-5 -> 0-100 if needed
                pace_score = float(raw_score) * 20.0 if isinstance(raw_score, (int, float)) and raw_score <= 5 else float(raw_score or 0.0)
                wpm = float(pace_result.get("wpm", 0.0))
                
                # Determine pace category
                if wpm < 120:
                    pace_category = "too_slow"
                    recommendations = ["Try speaking slightly faster", "Practice with a metronome"]
                elif wpm > 200:
                    pace_category = "too_fast"
                    recommendations = ["Slow down for better clarity", "Take more pauses between thoughts"]
                else:
                    pace_category = "optimal"
                    recommendations = ["Maintain current pace", "Consider slight variation for emphasis"]
                
                data = PaceAnalysisResponse(
                    question_attempt_id=question_attempt_id,
                    pace_score=float(pace_score),
                    words_per_minute=float(wpm),
                    pace_feedback=str(feedback),
                    pace_category=pace_category,
                    recommendations=recommendations
                ).model_dump()
                
            elif analysis_type == "pause":
                # Get word-level timestamps for pause analysis
                words_data = question_attempt.transcription.get("words", [])
                if not words_data:
                    raise ValueError("No word-level timestamps available for pause analysis")

                logger.info(
                  "PAUSE ANALYSIS START | Attempt=%s | WordCount=%s",
                  question_attempt_id,
                  len(words_data),
                )
                # Call real pause analysis
                pause_result = await analyze_pauses_async({"words": words_data})

                logger.info(
                    "PAUSE ANALYSIS RESULT | Attempt=%s | ResultType=%s | ResultKeys=%s | Score=%s",
                    question_attempt_id,
                    type(pause_result).__name__,
                    list(pause_result.keys()) if isinstance(pause_result, dict) else None,
                    pause_result.get("score") if isinstance(pause_result, dict) else None,
                )
                if not pause_result:
                    raise ValueError("Pause analysis failed to process word timestamps")
                
                raw_pause_score = pause_result.get('score')
                pause_score = float(raw_pause_score) * 20.0 if isinstance(raw_pause_score, (int, float)) and raw_pause_score <= 5 else float(raw_pause_score or 0.0)
                raw_distribution = pause_result.get('distribution') or {}
                default_distribution = {
                   "long": "0.0%",
                   "rushed": "0.0%",
                   "strategic": "0.0%",
                   "normal": "100.0%"
                }
# Merge defaults so required keys are never missing
                final_distribution = {**default_distribution, **raw_distribution}

                data = PauseAnalysisResponse(
                    question_attempt_id=question_attempt_id,
                    overview=pause_result.get('overview', 'Pause analysis completed'),
                    details=pause_result.get('details', []),
                    distribution=final_distribution,
                    actionable_feedback=pause_result.get('actionable_feedback', 'Continue using natural pauses'),
                    pause_score=pause_score,
                ).model_dump()
                
            else:
                raise ValueError(f"Unknown analysis type: {analysis_type}")
               
            return {
                "success": True,
                "error": None,
                "data": data
            }
            
        except Exception as e:
            logger.exception(
               "ANALYSIS GENERATION EXCEPTION | Attempt=%s | Type=%s | Error=%s",
               question_attempt_id,
               analysis_type,
               str(e),
            )
            return {
                "success": False,
                "error": f"Error generating {analysis_type} analysis: {str(e)}",
                "data": None
            }
    
    def _build_aggregated_analysis(
        self, 
        analysis_results: Dict[str, Dict[str, Any]]
    ) -> AggregatedAnalysis:
        """Build aggregated analysis from individual results."""
        logger.info(
        "AGGREGATION BUILD START | Results=%s",
        {
            k: {
                "success": v.get("success"),
                "data_type": type(v.get("data")).__name__,
                "data_keys": (
                    list(v.get("data").keys())
                    if isinstance(v.get("data"), dict)
                    else None
                ),
                "error": v.get("error"),
            }
            for k, v in analysis_results.items()
        },
        )
        # Get valid field names from the AggregatedAnalysis model
        valid_fields = set(AggregatedAnalysis.model_fields.keys())

        logger.info(
           "AGGREGATION MODEL FIELDS | Fields=%s",
           valid_fields,
        )
        # Build payload dict with only valid model fields
        payload = {}
        for analysis_type, result in analysis_results.items():
            logger.info(
              "AGGREGATION PROCESSING | Type=%s | Success=%s",
              analysis_type,
              result.get("success"),
            )
            if analysis_type in valid_fields:
                if result.get("success", False) and result.get("data"):
                    payload[analysis_type] = result["data"]

                    logger.info(
                    "AGGREGATION PAYLOAD ADDED | Type=%s | DataKeys=%s",
                    analysis_type,
                    list(result["data"].keys())
                    if isinstance(result["data"], dict)
                    else None,
                    )
                else:
                    payload[analysis_type] = None
                    logger.warning(
                    "AGGREGATION PAYLOAD NULL | Type=%s | Error=%s",
                    analysis_type,
                    result.get("error"),
                    )

            else:
                logger.warning(
                "AGGREGATION UNKNOWN MODEL FIELD | Type=%s | ValidFields=%s",
                analysis_type,
                valid_fields,
                )

            logger.info(
              "AGGREGATION FINAL PAYLOAD | Payload=%s",
               payload,
            )        
        # Construct and validate AggregatedAnalysis instance
        # return AggregatedAnalysis(**payload)
        try:

           aggregated = AggregatedAnalysis(**payload)

           logger.info(
            "AGGREGATION PYDANTIC SUCCESS | Fields=%s",
            list(aggregated.model_dump().keys()),
           )

           return aggregated

        except pydantic.ValidationError as e:

            logger.error(
            "AGGREGATION PYDANTIC VALIDATION FAILED | Errors=%s | Payload=%s",
            e.errors(),
            payload,
            )

            logger.exception(
            "AGGREGATION PYDANTIC TRACEBACK"
            )
        raise
    
    async def _save_analysis_to_db(
        self,
        question_attempt: QuestionAttempt,
        aggregated_analysis: AggregatedAnalysis,
        db: AsyncSession
    ) -> Tuple[bool, str | None]:
        """Save aggregated analysis to database."""
        logger.info(
        "ANALYSIS DB SAVE START | Attempt=%s",
        question_attempt.id,
        )
        try:
            # Convert Pydantic model to dict for JSON storage
            new_dict = aggregated_analysis.model_dump(exclude_none=True)
            logger.info(
            "ANALYSIS DB SERIALIZED | Attempt=%s | Keys=%s",
            question_attempt.id,
            list(new_dict.keys()),
            )
            # Merge with existing JSON
            existing = question_attempt.analysis_json or {}
            merged = dict(existing)
            for k, v in new_dict.items():
                merged[k] = v

            logger.info(
               "ANALYSIS DB MERGED JSON | Attempt=%s | Keys=%s",
               question_attempt.id,
               list(merged.keys()),
            )

            # Update the question attempt using SQL update to avoid session issues
            stmt = (
                sqlalchemy.update(QuestionAttempt)
                .where(QuestionAttempt.id == question_attempt.id)
                .values(analysis_json=merged)
            )
            await db.execute(stmt)
            await db.commit()
            logger.info(
            "ANALYSIS DB SAVE SUCCESS | Attempt=%s",
            question_attempt.id,
            )
            return True, None
            
        except Exception as e:
            await db.rollback()
            return False, str(e)


# Create service instance
analysis_service = AnalysisAggregationService()
