"""Service to build the Restructured Summary Report directly from per-question analyses.

This generates the new report format with:
- reportId (UUID)
- candidateInfo
- scoreSummary (with numeric scores and percentages)
- overallFeedback (speech fluency only)
- questionAnalysis (per-question feedback, null if not attempted)
"""

from __future__ import annotations

import uuid
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession
import sqlalchemy

from src.models.db.question_attempt import QuestionAttempt
from src.models.db.interview_question import InterviewQuestion
from src.models.db.interview import Interview
from src.models.schemas.summary_report import (
    SummaryReportResponse,
    CandidateInfo,
    ScoreSummary,
    KnowledgeCompetenceScore,
    ScoreCriteria,
    SpeechAndStructureScore,
    SpeechCriteria,
    OverallFeedback,
    SpeechFluencyFeedback,
    ActionableStep,
    QuestionAnalysisItem,
    QuestionFeedback,
    QuestionFeedbackSubsection,
)
from src.models.schemas.summary_report_v2 import (
    SummaryReportResponseLite,
    QuestionAnalysisItemLite,
    QuestionFeedbackLite,
    CandidateInfoLite,
)
from src.services.llm import synthesize_summary_sections, synthesize_summary_sections_lite


def _as_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _as_list_str(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x is not None]
    return [str(v)]


def _avg(nums: List[float]) -> Optional[float]:
    return sum(nums) / len(nums) if nums else None


def _to_int_0_5(score_pct: Optional[float]) -> int:
    """Convert 0-100 percentage to 0-5 integer score."""
    if score_pct is None:
        return 0
    # Convert percentage to 0-5 scale and round
    return round(max(0.0, min(100.0, score_pct)) / 20.0)


def _unique(items: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for s in items:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _question_type_label(category: str | None) -> str:
    key = (category or "tech").lower()
    category_map = {
        "tech": "Technical question",
        "tech_allied": "Technical Allied question",
        "behavioral": "Behavioral question",
        "self": "Self question",
        "productivity": "Productivity question",
        "company_candidate": "Company and Candidate question",
        "general": "General question",
    }
    return category_map.get(key, "Technical question")


def _is_non_tech_track(track: str | None) -> bool:
    normalized_track = (track or "").strip().lower()
    return normalized_track.startswith("non-tech:")


class SummaryReportServiceV2:
    def __init__(self, db: SQLAlchemyAsyncSession) -> None:
        self._db = db

    def _order_questions_with_followups(self, questions: List[InterviewQuestion]) -> List[InterviewQuestion]:
        """Reorder questions so follow-ups appear immediately after their parent question.
        
        For example, if Q4 has a follow-up that was created as Q6:
        - Original order: Q1, Q2, Q3, Q4, Q5, Q6(follow-up to Q4)
        - Reordered: Q1, Q2, Q3, Q4, Q6(follow-up to Q4), Q5
        
        This ensures the report shows questions in logical order with follow-ups
        grouped with their parent questions.
        """
        # Separate into base questions and follow-ups
        base_questions: List[InterviewQuestion] = []
        follow_ups_by_parent: Dict[int, List[InterviewQuestion]] = {}
        
        for q in questions:
            if q.is_follow_up and q.parent_question_id is not None:
                if q.parent_question_id not in follow_ups_by_parent:
                    follow_ups_by_parent[q.parent_question_id] = []
                follow_ups_by_parent[q.parent_question_id].append(q)
            else:
                base_questions.append(q)
        
        # Sort follow-ups for each parent by their order (in case of multiple follow-ups)
        for parent_id in follow_ups_by_parent:
            follow_ups_by_parent[parent_id].sort(key=lambda x: x.order)
        
        # Build final list: insert follow-ups after their parent
        result: List[InterviewQuestion] = []
        for q in base_questions:
            result.append(q)
            # Add any follow-ups for this question immediately after
            if q.id in follow_ups_by_parent:
                result.extend(follow_ups_by_parent[q.id])
        
        return result

    def _maybe_strip_knowledge_summary(self, *, track: str, score_summary: Dict[str, Any]) -> Dict[str, Any]:
        if _is_non_tech_track(track):
            score_summary.pop("knowledgeCompetence", None)
        return score_summary

    async def generate_for_interview(
        self,
        interview_id: int,
        question_attempts: Iterable[QuestionAttempt],
        track: str,
        resume_used: bool | None = None,
        candidate_name: str | None = None,
    ) -> Dict[str, Any]:
        """Generate the new restructured summary report."""
        
        question_attempts = list(question_attempts)
        
        # Fetch all InterviewQuestions for this interview
        stmt = sqlalchemy.select(InterviewQuestion).where(
            InterviewQuestion.interview_id == interview_id
        ).order_by(InterviewQuestion.order.asc())
        result = await self._db.execute(stmt)
        fetched_questions = list(result.scalars().all())
        
        # Reorder questions so follow-ups appear immediately after their parent question
        # This ensures Q4's follow-up appears as Q4.1 (after Q4) rather than at the end
        all_interview_questions = self._order_questions_with_followups(fetched_questions)
        
        # Build a map of question_id -> QuestionAttempt
        # When there are multiple attempts for the same question (re-attempts),
        # keep only the latest attempt as it represents the user's most recent/best effort
        attempts_by_question_id: Dict[int, QuestionAttempt] = {}
        actually_attempted_question_ids: set[int] = set()  # Track questions with actual content
        
        for qa in question_attempts:
            if qa.question_id is not None:
                # This overwrites previous attempts - intended behavior for re-attempts
                attempts_by_question_id[qa.question_id] = qa
                
                # Check if this attempt has actual content
                has_transcription = qa.transcription is not None and len(str(qa.transcription).strip()) > 0
                analysis = getattr(qa, "analysis_json", None) or {}
                has_analysis = bool(analysis)
                
                if has_transcription or has_analysis:
                    actually_attempted_question_ids.add(qa.question_id)
        
        total_questions = len(all_interview_questions)
        attempted_questions = len(actually_attempted_question_ids)  # Count only real attempts
        
        # Collect metrics from analyses
        kc_accuracy: List[float] = []
        kc_depth: List[float] = []
        kc_relevance: List[float] = []
        kc_examples: List[float] = []
        kc_terminology: List[float] = []
        
        ssf_fluency: List[float] = []
        ssf_structure: List[float] = []
        ssf_pacing: List[float] = []
        ssf_grammar: List[float] = []
        
        # Collect feedback strings
        speech_strengths: List[str] = []
        speech_improvements: List[str] = []
        
        # Build per-question inputs for LLM
        per_question_inputs: List[dict] = []
        
        for interview_question in all_interview_questions:
            qa = attempts_by_question_id.get(interview_question.id)
            
            # Skip questions that weren't actually attempted with content
            if interview_question.id not in actually_attempted_question_ids:
                continue
            
            # Double-check that the latest attempt has actual content
            # This handles re-attempt scenarios where an earlier session had content
            # but the latest attempt is empty
            if qa is None:
                continue
                
            has_transcription = bool(
                qa.transcription and isinstance(qa.transcription, dict) and qa.transcription.get("text")
            )
            has_analysis = bool(qa.analysis_json and isinstance(qa.analysis_json, dict))
            
            if not (has_transcription or has_analysis):
                continue
            
            # Attempted question with actual content - process analysis
            analysis: Dict[str, Any] = getattr(qa, "analysis_json", None) or {}
            
            # Domain/knowledge metrics
            d = analysis.get("domain") or {}
            criteria = d.get("criteria") or {}
            
            # Extract scores for each criterion (0-100 scale from LLM)
            accuracy_score = _as_float(((criteria.get("correctness") or {}).get("score")))
            if accuracy_score is not None:
                kc_accuracy.append(max(0.0, min(100.0, accuracy_score)))
            
            depth_score = _as_float(((criteria.get("depth") or {}).get("score")))
            if depth_score is not None:
                kc_depth.append(max(0.0, min(100.0, depth_score)))
            
            relevance_score = _as_float(((criteria.get("relevance") or {}).get("score")))
            if relevance_score is not None:
                kc_relevance.append(max(0.0, min(100.0, relevance_score)))
            
            # Examples and terminology - may not always be present
            examples_score = _as_float(((criteria.get("examples") or {}).get("score")))
            if examples_score is not None:
                kc_examples.append(max(0.0, min(100.0, examples_score)))
            
            terminology_score = _as_float(((criteria.get("terminology") or {}).get("score")))
            if terminology_score is not None:
                kc_terminology.append(max(0.0, min(100.0, terminology_score)))
            
            # Communication/speech metrics
            c = analysis.get("communication") or {}
            ccrit = c.get("criteria") or {}
            
            structure_val = _as_float(c.get("structure_score") or (ccrit.get("structure", {}) or {}).get("score"))
            if structure_val is not None:
                ssf_structure.append(max(0.0, min(100.0, structure_val)))
            
            grammar_val = _as_float(c.get("grammar_score") or (ccrit.get("grammar", {}) or {}).get("score"))
            if grammar_val is not None:
                ssf_grammar.append(max(0.0, min(100.0, grammar_val)))
            
            # Pace/Pause analyses
            p = analysis.get("pace") or {}
            z = analysis.get("pause") or {}
            
            pace_raw = p.get("score")
            if isinstance(pace_raw, (int, float)):
                pace_scaled = pace_raw * 20 if pace_raw <= 5 else pace_raw
                ssf_pacing.append(max(0.0, min(100.0, pace_scaled)))
            
            # Collect speech feedback
            speech_strengths.extend(_as_list_str(c.get("strengths")))
            speech_improvements.extend(_as_list_str(c.get("recommendations")))
            speech_improvements.extend(_as_list_str(p.get("recommendations")))
            speech_improvements.extend(_as_list_str(z.get("recommendations")))
            
            # Build input for LLM
            per_question_inputs.append({
                "questionId": interview_question.id,
                "questionAttemptId": qa.id,
                "questionText": interview_question.text,
                "questionCategory": interview_question.category,
                "attempted": True,
                "domain": d,
                "communication": c,
                "pace": p,
                "pause": z,
            })
        
        # Compute aggregated metrics
        computed_metrics = {
            "kc_accuracy_pct": _avg(kc_accuracy),
            "kc_depth_pct": _avg(kc_depth),
            "kc_relevance_pct": _avg(kc_relevance),
            "kc_examples_pct": _avg(kc_examples) if kc_examples else 0.0,
            "kc_terminology_pct": _avg(kc_terminology) if kc_terminology else 0.0,
            "ssf_fluency_pct": _avg(ssf_fluency) if ssf_fluency else _avg([
                x for x in [_avg(ssf_structure), _avg(ssf_grammar)] if x is not None
            ]),
            "ssf_structure_pct": _avg(ssf_structure),
            "ssf_pacing_pct": _avg(ssf_pacing),
            "ssf_grammar_pct": _avg(ssf_grammar),
            "total_questions": total_questions,
            "attempted_questions": attempted_questions,  # Count only questions with actual content
            "speech_strengths": _unique(speech_strengths)[:6],
            "speech_improvements": _unique(speech_improvements)[:6],
        }
        
        # Call LLM to synthesize the report
        interview_date = datetime.now(timezone.utc).isoformat()
        llm_data, llm_error, latency_ms, model_name = await synthesize_summary_sections(
            per_question_inputs=per_question_inputs,
            computed_metrics=computed_metrics,
            max_questions=None,  # Include all questions
            interview_track=track,
            interview_date=interview_date,
            candidate_name=candidate_name,
            total_questions=total_questions,
        )
        
        # If LLM failed, build a fallback report
        if not llm_data or llm_error:
            return self._build_fallback_report(
                interview_id=interview_id,
                track=track,
                computed_metrics=computed_metrics,
                all_questions=all_interview_questions,
                attempts_map=attempts_by_question_id,
                interview_date=interview_date,
                candidate_name=candidate_name,
            )
        
        # Calculate final scores from LLM's per-question scores
        try:
            final_report = self._calculate_final_scores(
                llm_data=llm_data,
                total_questions=total_questions,
                all_questions=all_interview_questions,
                attempts_by_question_id=attempts_by_question_id,
                actually_attempted_question_ids=actually_attempted_question_ids,
                track=track,
                interview_date=interview_date,
                candidate_name=candidate_name,
            )
            # Validate the final report
            parsed = SummaryReportResponse(**final_report)
            return parsed.model_dump(exclude_none=True)
        except Exception as e:
            # LLM data invalid or calculation failed - fall back
            return self._build_fallback_report(
                interview_id=interview_id,
                track=track,
                computed_metrics=computed_metrics,
                all_questions=all_interview_questions,
                attempts_map=attempts_by_question_id,
                interview_date=interview_date,
                candidate_name=candidate_name,
            )
    
    def _calculate_final_scores(
        self,
        llm_data: Dict[str, Any],
        total_questions: int,
        all_questions: List[InterviewQuestion],
        attempts_by_question_id: Dict[int, QuestionAttempt],
        actually_attempted_question_ids: set[int],
        track: str,
        interview_date: str,
        candidate_name: str | None,
    ) -> Dict[str, Any]:
        """Calculate final scores with attempt-based penalty from LLM's per-question scores.
        
        Also builds reportId, candidateInfo, and questionAnalysis from code (not LLM).
        
        Formula:
        1. Sum individual criterion scores across all attempted questions
        2. Apply attempt penalty: final_score = sum × (attempted / total)
        3. Calculate percentage: (final_score / maxScore) × 100
        """
        per_question_scores = llm_data.get("perQuestionScores", [])
        per_question_feedback = llm_data.get("perQuestionFeedback", [])
        attempted_questions = len(per_question_scores)
        
        # If no questions were actually attempted, return all zeros immediately
        if attempted_questions == 0 or total_questions == 0:
            score_summary = {
                "knowledgeCompetence": {
                    "score": 0,
                    "maxScore": 25,
                    "average": 0.0,
                    "maxAverage": 5.0,
                    "percentage": 0,
                    "criteria": {
                        "accuracy": 0,
                        "depth": 0,
                        "relevance": 0,
                        "examples": 0,
                        "terminology": 0,
                    }
                },
                "speechAndStructure": {
                    "score": 0,
                    "maxScore": 20,
                    "average": 0.0,
                    "maxAverage": 5.0,
                    "percentage": 0,
                    "criteria": {
                        "fluency": 0,
                        "structure": 0,
                        "pacing": 0,
                        "grammar": 0,
                    }
                }
            }
            score_summary = self._maybe_strip_knowledge_summary(track=track, score_summary=score_summary)
            
            candidate_info = {
                "name": candidate_name,
                "interviewDate": interview_date,
                "roleTopic": track.title(),
            }
            
            # Build questionAnalysis with "Not attempted" feedback for unattempted questions
            question_analysis = []
            
            for idx, iq in enumerate(all_questions):
                category_map = {
                    "tech": "Technical question",
                    "tech_allied": "Technical Allied question", 
                    "behavioral": "Behavioral question",
                }
                question_type = category_map.get(iq.category, "Technical question")
                
                # Check if attempted
                if iq.id not in actually_attempted_question_ids:
                    feedback = {
                        "knowledgeRelated": {
                            "strengths": [],
                            "areasOfImprovement": ["Not attempted"],
                            "actionableInsights": []
                        }
                    }
                else:
                    feedback = None
                
                question_analysis.append({
                    "id": idx + 1,
                    "totalQuestions": total_questions,
                    "type": question_type,
                    "question": iq.text,
                    "feedback": feedback,
                })
            
            # Return early with all zeros
            overall_feedback = {
                "speechFluency": {
                    "strengths": [],
                    "areasOfImprovement": [],
                    "actionableSteps": [
                        {
                            "title": "Attempt All Questions",
                            "description": "Complete the interview by answering all questions to receive personalized feedback on your performance."
                        }
                    ],
                }
            }
            
            return {
                "reportId": str(uuid.uuid4()),
                "candidateInfo": candidate_info,
                "scoreSummary": score_summary,
                "overallFeedback": overall_feedback,
                "questionAnalysis": question_analysis,
            }
        
        # Sum knowledge scores across all attempted questions
        kc_accuracy_sum = 0
        kc_depth_sum = 0
        kc_relevance_sum = 0
        kc_examples_sum = 0
        kc_terminology_sum = 0
        
        # Sum speech scores across all attempted questions
        ssf_fluency_sum = 0
        ssf_structure_sum = 0
        ssf_pacing_sum = 0
        ssf_grammar_sum = 0
        
        for q_scores in per_question_scores:
            k_scores = q_scores.get("knowledgeScores", {})
            kc_accuracy_sum += k_scores.get("accuracy", 0)
            kc_depth_sum += k_scores.get("depth", 0)
            kc_relevance_sum += k_scores.get("relevance", 0)
            kc_examples_sum += k_scores.get("examples", 0)
            kc_terminology_sum += k_scores.get("terminology", 0)
            
            s_scores = q_scores.get("speechScores", {})
            ssf_fluency_sum += s_scores.get("fluency", 0)
            ssf_structure_sum += s_scores.get("structure", 0)
            ssf_pacing_sum += s_scores.get("pacing", 0)
            ssf_grammar_sum += s_scores.get("grammar", 0)
        
                # Calculate total sums from attempted questions
        kc_total_from_attempted = (kc_accuracy_sum + kc_depth_sum + kc_relevance_sum + 
                                   kc_examples_sum + kc_terminology_sum)
        ssf_total_from_attempted = (ssf_fluency_sum + ssf_structure_sum + 
                                    ssf_pacing_sum + ssf_grammar_sum)
        
        # Calculate average scores per question from attempted questions
        # IMPORTANT: This uses attempted_questions (not total_questions) as denominator
        # to get the average performance on attempted questions
        if attempted_questions > 0:
            kc_avg_per_question = kc_total_from_attempted / attempted_questions
            ssf_avg_per_question = ssf_total_from_attempted / attempted_questions
        else:
            kc_avg_per_question = 0
            ssf_avg_per_question = 0
        
        # Apply completion penalty: scale by the ratio of attempted to total questions
        # This penalizes users who skip questions
        # Example 1 (RE-ATTEMPT): 2 total questions, both attempted initially, then re-attempt Q2
        #   - attempted=2, total=2, ratio=1.0 → no penalty (all questions attempted)
        # Example 2 (SKIP): 5 total questions, only 2 attempted, 3 skipped
        #   - attempted=2, total=5, ratio=0.4 → 40% penalty (60% of questions skipped)
        completion_ratio = attempted_questions / total_questions if total_questions > 0 else 0.0
        
        kc_score_total = round(kc_avg_per_question * completion_ratio)
        ssf_score_total = round(ssf_avg_per_question * completion_ratio)
        
        # Distribute the final penalized score back to criteria proportionally
        # This ensures criteria sum exactly to the final score
        if kc_total_from_attempted > 0:
            kc_accuracy_final = round(kc_score_total * (kc_accuracy_sum / kc_total_from_attempted))
            kc_depth_final = round(kc_score_total * (kc_depth_sum / kc_total_from_attempted))
            kc_relevance_final = round(kc_score_total * (kc_relevance_sum / kc_total_from_attempted))
            kc_examples_final = round(kc_score_total * (kc_examples_sum / kc_total_from_attempted))
            # Give remaining to last criterion to ensure exact sum
            kc_terminology_final = kc_score_total - (kc_accuracy_final + kc_depth_final + kc_relevance_final + kc_examples_final)
        else:
            kc_accuracy_final = kc_depth_final = kc_relevance_final = kc_examples_final = kc_terminology_final = 0
        
        if ssf_total_from_attempted > 0:
            ssf_fluency_final = round(ssf_score_total * (ssf_fluency_sum / ssf_total_from_attempted))
            ssf_structure_final = round(ssf_score_total * (ssf_structure_sum / ssf_total_from_attempted))
            ssf_pacing_final = round(ssf_score_total * (ssf_pacing_sum / ssf_total_from_attempted))
            # Give remaining to last criterion to ensure exact sum
            ssf_grammar_final = ssf_score_total - (ssf_fluency_final + ssf_structure_final + ssf_pacing_final)
        else:
            ssf_fluency_final = ssf_structure_final = ssf_pacing_final = ssf_grammar_final = 0
        
        # Calculate averages
        kc_avg = kc_score_total / 5.0
        ssf_avg = ssf_score_total / 4.0
        
        # Calculate percentages (max: KC=25, SSF=20)
        kc_pct = int((kc_score_total / 25.0) * 100)
        ssf_pct = int((ssf_score_total / 20.0) * 100)
        
        # Build the score summary
        score_summary = {
            "knowledgeCompetence": {
                "score": kc_score_total,
                "maxScore": 25,
                "average": round(kc_avg, 2),
                "maxAverage": 5.0,
                "percentage": kc_pct,
                "criteria": {
                    "accuracy": kc_accuracy_final,
                    "depth": kc_depth_final,
                    "relevance": kc_relevance_final,
                    "examples": kc_examples_final,
                    "terminology": kc_terminology_final,
                }
            },
            "speechAndStructure": {
                "score": ssf_score_total,
                "maxScore": 20,
                "average": round(ssf_avg, 2),
                "maxAverage": 5.0,
                "percentage": ssf_pct,
                "criteria": {
                    "fluency": ssf_fluency_final,
                    "structure": ssf_structure_final,
                    "pacing": ssf_pacing_final,
                    "grammar": ssf_grammar_final,
                }
            }
        }
        score_summary = self._maybe_strip_knowledge_summary(track=track, score_summary=score_summary)
        
        # Build candidateInfo in code
        candidate_info = {
            "name": candidate_name,
            "interviewDate": interview_date,
            "roleTopic": track.title(),
        }
        
        # Build questionAnalysis from code (question metadata) + LLM (feedback)
        # Create a map of questionId to feedback from LLM
        feedback_by_question_id = {}
        for idx, score in enumerate(per_question_scores):
            q_id = score.get("questionId")
            if idx < len(per_question_feedback):
                llm_feedback = per_question_feedback[idx]
                if llm_feedback is not None:
                    # Extract knowledge-related feedback
                    feedback_data = llm_feedback.get("knowledgeRelated", {})
                    
                    # Ensure all required fields exist with empty arrays as fallback
                    strengths = feedback_data.get("strengths") or []
                    areas_of_improvement = feedback_data.get("areasOfImprovement") or []
                    actionable_insights = feedback_data.get("actionableInsights") or []
                    
                    # Always include feedback structure, even if empty
                    # This ensures consistent API response structure
                    feedback_by_question_id[q_id] = {
                        "knowledgeRelated": {
                            "strengths": strengths if isinstance(strengths, list) else [],
                            "areasOfImprovement": areas_of_improvement if isinstance(areas_of_improvement, list) else [],
                            "actionableInsights": actionable_insights if isinstance(actionable_insights, list) else [],
                        }
                    }
        
        question_analysis = []
        
        for idx, iq in enumerate(all_questions):
            question_type = _question_type_label(iq.category)
            
            # Check if this question was attempted with valid content
            attempt = attempts_by_question_id.get(iq.id)
            has_valid_attempt = False
            
            if attempt is not None:
                has_transcription = bool(
                    attempt.transcription and isinstance(attempt.transcription, dict) and attempt.transcription.get("text")
                )
                has_analysis = bool(attempt.analysis_json and isinstance(attempt.analysis_json, dict))
                has_valid_attempt = has_transcription or has_analysis
            
            # Get feedback from LLM if available for this question
            feedback = feedback_by_question_id.get(iq.id)
            
            # If not attempted, provide "Not attempted" feedback
            if not has_valid_attempt:
                feedback = {
                    "knowledgeRelated": {
                        "strengths": [],
                        "areasOfImprovement": ["Not attempted"],
                        "actionableInsights": []
                    }
                }
            
            question_analysis.append({
                "id": idx + 1,  # Sequential numbering for all questions
                "totalQuestions": total_questions,  # Show total question count
                "type": question_type,
                "question": iq.text,
                "feedback": feedback,
            })
            
            display_index += 1
        
        # Ensure overallFeedback has proper structure with all required fields
        overall_feedback_raw = llm_data.get("overallFeedback", {})
        speech_fluency_raw = overall_feedback_raw.get("speechFluency", {}) if isinstance(overall_feedback_raw, dict) else {}
        
        # If no questions were actually attempted (all skipped), don't show misleading feedback
        # Clear out any generic LLM feedback and show only actionable step to attempt questions
        if attempted_questions == 0:
            overall_feedback = {
                "speechFluency": {
                    "strengths": [],
                    "areasOfImprovement": [],
                    "actionableSteps": [
                        {
                            "title": "Attempt All Questions",
                            "description": "Complete the interview by answering all questions to receive personalized feedback on your performance."
                        }
                    ],
                }
            }
        else:
            # Questions were attempted - use LLM feedback
            overall_feedback = {
                "speechFluency": {
                    "strengths": speech_fluency_raw.get("strengths") or [] if isinstance(speech_fluency_raw, dict) else [],
                    "areasOfImprovement": speech_fluency_raw.get("areasOfImprovement") or [] if isinstance(speech_fluency_raw, dict) else [],
                    "actionableSteps": speech_fluency_raw.get("actionableSteps") or [] if isinstance(speech_fluency_raw, dict) else [],
                }
            }
        
        # Return the complete report with calculated scores and code-generated metadata
        return {
            "reportId": str(uuid.uuid4()),  # Generate in code
            "candidateInfo": candidate_info,  # Generate in code
            "scoreSummary": score_summary,
            "overallFeedback": overall_feedback,
            "questionAnalysis": question_analysis,  # Built from code + LLM feedback
        }
    
    def _build_fallback_report(
        self,
        interview_id: int,
        track: str,
        computed_metrics: Dict[str, Any],
        all_questions: List[InterviewQuestion],
        attempts_map: Dict[int, QuestionAttempt],
        interview_date: str,
        candidate_name: str | None,
    ) -> Dict[str, Any]:
        """Build a fallback report when LLM fails."""
        
        # Generate report ID
        report_id = str(uuid.uuid4())
        
        # Candidate info
        candidate_info = CandidateInfo(
            name=candidate_name,
            interviewDate=interview_date,
            roleTopic=track.title(),
        )
        
        # Calculate base scores (from attempted questions average)
        kc_accuracy = _to_int_0_5(computed_metrics.get("kc_accuracy_pct"))
        kc_depth = _to_int_0_5(computed_metrics.get("kc_depth_pct"))
        kc_relevance = _to_int_0_5(computed_metrics.get("kc_relevance_pct"))
        kc_examples = _to_int_0_5(computed_metrics.get("kc_examples_pct"))
        kc_terminology = _to_int_0_5(computed_metrics.get("kc_terminology_pct"))
        
        ssf_fluency = _to_int_0_5(computed_metrics.get("ssf_fluency_pct"))
        ssf_structure = _to_int_0_5(computed_metrics.get("ssf_structure_pct"))
        ssf_pacing = _to_int_0_5(computed_metrics.get("ssf_pacing_pct"))
        ssf_grammar = _to_int_0_5(computed_metrics.get("ssf_grammar_pct"))
        
        # Calculate attempt penalty
        total_questions = computed_metrics.get("total_questions", len(all_questions))
        attempted_questions = computed_metrics.get("attempted_questions", len(attempts_map))
        
        if attempted_questions == 0 or total_questions == 0:
            attempt_ratio = 0.0
        else:
            attempt_ratio = attempted_questions / total_questions
        
        # Calculate totals BEFORE penalty
        kc_total_before_penalty = kc_accuracy + kc_depth + kc_relevance + kc_examples + kc_terminology
        ssf_total_before_penalty = ssf_fluency + ssf_structure + ssf_pacing + ssf_grammar
        
        # Apply attempt penalty to totals (not individual criteria)
        kc_score = round(kc_total_before_penalty * attempt_ratio)
        ssf_score = round(ssf_total_before_penalty * attempt_ratio)
        
        # Distribute the final penalized score back to criteria proportionally
        if kc_total_before_penalty > 0:
            kc_accuracy_final = round(kc_score * (kc_accuracy / kc_total_before_penalty))
            kc_depth_final = round(kc_score * (kc_depth / kc_total_before_penalty))
            kc_relevance_final = round(kc_score * (kc_relevance / kc_total_before_penalty))
            kc_examples_final = round(kc_score * (kc_examples / kc_total_before_penalty))
            # Give remaining to last criterion to ensure exact sum
            kc_terminology_final = kc_score - (kc_accuracy_final + kc_depth_final + kc_relevance_final + kc_examples_final)
        else:
            kc_accuracy_final = kc_depth_final = kc_relevance_final = kc_examples_final = kc_terminology_final = 0
        
        if ssf_total_before_penalty > 0:
            ssf_fluency_final = round(ssf_score * (ssf_fluency / ssf_total_before_penalty))
            ssf_structure_final = round(ssf_score * (ssf_structure / ssf_total_before_penalty))
            ssf_pacing_final = round(ssf_score * (ssf_pacing / ssf_total_before_penalty))
            # Give remaining to last criterion to ensure exact sum
            ssf_grammar_final = ssf_score - (ssf_fluency_final + ssf_structure_final + ssf_pacing_final)
        else:
            ssf_fluency_final = ssf_structure_final = ssf_pacing_final = ssf_grammar_final = 0
        
        # Calculate averages and percentages
        kc_avg = kc_score / 5.0
        kc_pct = int((kc_score / 25.0) * 100)
        
        ssf_avg = ssf_score / 4.0
        ssf_pct = int((ssf_score / 20.0) * 100)
        
        score_summary = ScoreSummary(
            knowledgeCompetence=KnowledgeCompetenceScore(
                score=kc_score,
                maxScore=25,
                average=round(kc_avg, 2),
                maxAverage=5.0,
                percentage=kc_pct,
                criteria=ScoreCriteria(
                    accuracy=kc_accuracy_final,
                    depth=kc_depth_final,
                    relevance=kc_relevance_final,
                    examples=kc_examples_final,
                    terminology=kc_terminology_final,
                ),
            ),
            speechAndStructure=SpeechAndStructureScore(
                score=ssf_score,
                maxScore=20,
                average=round(ssf_avg, 2),
                maxAverage=5.0,
                percentage=ssf_pct,
                criteria=SpeechCriteria(
                    fluency=ssf_fluency_final,
                    structure=ssf_structure_final,
                    pacing=ssf_pacing_final,
                    grammar=ssf_grammar_final,
                ),
            ),
        )
        score_summary_dict = score_summary.model_dump(exclude_none=False)
        score_summary_dict = self._maybe_strip_knowledge_summary(track=track, score_summary=score_summary_dict)
        
        # Overall feedback (speech only)
        speech_strengths = computed_metrics.get("speech_strengths", [])
        speech_improvements = computed_metrics.get("speech_improvements", [])
        
        # If no questions were actually attempted, don't show misleading feedback
        if attempted_questions == 0:
            overall_feedback = OverallFeedback(
                speechFluency=SpeechFluencyFeedback(
                    strengths=[],
                    areasOfImprovement=[],
                    actionableSteps=[
                        ActionableStep(
                            title="Attempt All Questions",
                            description="Complete the interview by answering all questions to receive personalized feedback on your performance.",
                        ),
                    ],
                ),
            )
        else:
            overall_feedback = OverallFeedback(
                speechFluency=SpeechFluencyFeedback(
                    strengths=speech_strengths[:4],
                    areasOfImprovement=speech_improvements[:4],
                    actionableSteps=[
                        ActionableStep(
                            title="Practice Regular Speaking",
                            description="Record yourself answering technical questions and review for fluency improvements.",
                        ),
                        ActionableStep(
                            title="Structured Response Framework",
                            description="Use a clear structure: state the problem, explain your approach, provide examples, and summarize.",
                        ),
                    ],
                ),
            )
        
        # Question analysis - only show attempted questions in sequential order
        question_analysis: List[QuestionAnalysisItem] = []
        
        for idx, interview_question in enumerate(all_questions):
            qa = attempts_map.get(interview_question.id)
            
            q_type = _question_type_label(interview_question.category)
            
            # Check if attempted
            if qa is None:
                # Not attempted
                question_analysis.append(QuestionAnalysisItem(
                    id=idx + 1,
                    totalQuestions=total_questions,
                    type=q_type,
                    question=interview_question.text,
                    feedback=QuestionFeedback(
                        knowledgeRelated=QuestionFeedbackSubsection(
                            strengths=[],
                            areasOfImprovement=["Not attempted"],
                            actionableInsights=[],
                        ),
                    ),
                ))
                continue
            
            # Double-check for valid content
            has_transcription = bool(
                qa.transcription and isinstance(qa.transcription, dict) and qa.transcription.get("text")
            )
            has_analysis = bool(qa.analysis_json and isinstance(qa.analysis_json, dict))
            
            if not (has_transcription or has_analysis):
                # No valid content
                question_analysis.append(QuestionAnalysisItem(
                    id=idx + 1,
                    totalQuestions=total_questions,
                    type=q_type,
                    question=interview_question.text,
                    feedback=QuestionFeedback(
                        knowledgeRelated=QuestionFeedbackSubsection(
                            strengths=[],
                            areasOfImprovement=["Not attempted"],
                            actionableInsights=[],
                        ),
                    ),
                ))
                continue
            
            # Attempted - extract feedback from analysis
            analysis = getattr(qa, "analysis_json", None) or {}
            d = analysis.get("domain") or {}
            
            strengths = _as_list_str(d.get("strengths"))[:3]
            improvements = _as_list_str(d.get("improvements"))[:3]
            
            question_analysis.append(QuestionAnalysisItem(
                id=idx + 1,
                totalQuestions=total_questions,
                type=q_type,
                question=interview_question.text,
                feedback=QuestionFeedback(
                    knowledgeRelated=QuestionFeedbackSubsection(
                        strengths=strengths,
                        areasOfImprovement=improvements,
                        actionableInsights=[
                            ActionableStep(
                                title="Deepen Understanding",
                                description="Review core concepts related to this question and practice explaining them clearly.",
                            ),
                        ],
                    ),
                ),
            ))
        
        # Build final response
        report = SummaryReportResponse(
            reportId=report_id,
            candidateInfo=candidate_info,
            scoreSummary=ScoreSummary(**score_summary_dict),
            overallFeedback=overall_feedback,
            questionAnalysis=question_analysis,
        )
        
        return report.model_dump(exclude_none=True)

    async def generate_for_interview_lite(
        self,
        interview_id: int,
        question_attempts: Iterable[QuestionAttempt],
        track: str,
        resume_used: bool | None = None,
        candidate_name: str | None = None,
    ) -> Dict[str, Any]:
        """Generate the new restructured summary report (Lite)."""
        
        question_attempts = list(question_attempts)
        
        # Fetch interview for duration
        interview_stmt = sqlalchemy.select(Interview).where(Interview.id == interview_id)
        interview_res = await self._db.execute(interview_stmt)
        interview = interview_res.scalar_one_or_none()
        
        duration_str = "0 mins"
        duration_feedback = "You managed your time effectively."
        
        if interview and interview.created_at:
            start_time = interview.created_at
            end_time = start_time
            for qa in question_attempts:
                if qa.created_at and qa.created_at > end_time:
                    end_time = qa.created_at
            
            # Ensure end_time is timezone aware if start_time is
            if end_time.tzinfo is None and start_time.tzinfo is not None:
                end_time = end_time.replace(tzinfo=start_time.tzinfo)
                
            diff = (end_time - start_time).total_seconds()
            mins = int(diff / 60)
            if mins < 1: mins = 1
            duration_str = f"{mins} mins"
        
        # Fetch all InterviewQuestions for this interview
        stmt = sqlalchemy.select(InterviewQuestion).where(
            InterviewQuestion.interview_id == interview_id
        ).order_by(InterviewQuestion.order.asc())
        result = await self._db.execute(stmt)
        all_interview_questions = list(result.scalars().all())
        
        # Build a map of question_id -> QuestionAttempt
        attempts_by_question_id: Dict[int, QuestionAttempt] = {}
        actually_attempted_question_ids: set[int] = set()
        
        for qa in question_attempts:
            if qa.question_id is not None:
                attempts_by_question_id[qa.question_id] = qa
                has_transcription = qa.transcription is not None and len(str(qa.transcription).strip()) > 0
                analysis = getattr(qa, "analysis_json", None) or {}
                has_analysis = bool(analysis)
                if has_transcription or has_analysis:
                    actually_attempted_question_ids.add(qa.question_id)
        
        total_questions = len(all_interview_questions)
        attempted_questions = len(actually_attempted_question_ids)
        
        # Collect metrics
        kc_accuracy: List[float] = []
        kc_depth: List[float] = []
        kc_relevance: List[float] = []
        kc_examples: List[float] = []
        kc_terminology: List[float] = []
        ssf_fluency: List[float] = []
        ssf_structure: List[float] = []
        ssf_pacing: List[float] = []
        ssf_grammar: List[float] = []
        speech_strengths: List[str] = []
        speech_improvements: List[str] = []
        per_question_inputs: List[dict] = []
        
        for interview_question in all_interview_questions:
            qa = attempts_by_question_id.get(interview_question.id)
            if interview_question.id not in actually_attempted_question_ids:
                continue
            if qa is None:
                continue
            
            analysis: Dict[str, Any] = getattr(qa, "analysis_json", None) or {}
            d = analysis.get("domain") or {}
            criteria = d.get("criteria") or {}
            
            accuracy_score = _as_float(((criteria.get("correctness") or {}).get("score")))
            if accuracy_score is not None: kc_accuracy.append(max(0.0, min(100.0, accuracy_score)))
            depth_score = _as_float(((criteria.get("depth") or {}).get("score")))
            if depth_score is not None: kc_depth.append(max(0.0, min(100.0, depth_score)))
            relevance_score = _as_float(((criteria.get("relevance") or {}).get("score")))
            if relevance_score is not None: kc_relevance.append(max(0.0, min(100.0, relevance_score)))
            examples_score = _as_float(((criteria.get("examples") or {}).get("score")))
            if examples_score is not None: kc_examples.append(max(0.0, min(100.0, examples_score)))
            terminology_score = _as_float(((criteria.get("terminology") or {}).get("score")))
            if terminology_score is not None: kc_terminology.append(max(0.0, min(100.0, terminology_score)))
            
            c = analysis.get("communication") or {}
            ccrit = c.get("criteria") or {}
            structure_val = _as_float(c.get("structure_score") or (ccrit.get("structure", {}) or {}).get("score"))
            if structure_val is not None: ssf_structure.append(max(0.0, min(100.0, structure_val)))
            grammar_val = _as_float(c.get("grammar_score") or (ccrit.get("grammar", {}) or {}).get("score"))
            if grammar_val is not None: ssf_grammar.append(max(0.0, min(100.0, grammar_val)))
            
            p = analysis.get("pace") or {}
            z = analysis.get("pause") or {}
            pace_raw = p.get("score")
            if isinstance(pace_raw, (int, float)):
                pace_scaled = pace_raw * 20 if pace_raw <= 5 else pace_raw
                ssf_pacing.append(max(0.0, min(100.0, pace_scaled)))
            
            speech_strengths.extend(_as_list_str(c.get("strengths")))
            speech_improvements.extend(_as_list_str(c.get("recommendations")))
            speech_improvements.extend(_as_list_str(p.get("recommendations")))
            speech_improvements.extend(_as_list_str(z.get("recommendations")))
            
            per_question_inputs.append({
                "questionId": interview_question.id,
                "questionAttemptId": qa.id,
                "questionText": interview_question.text,
                "questionCategory": interview_question.category,
                "attempted": True,
                "domain": d,
                "communication": c,
                "pace": p,
                "pause": z,
            })
            
        computed_metrics = {
            "kc_accuracy_pct": _avg(kc_accuracy),
            "kc_depth_pct": _avg(kc_depth),
            "kc_relevance_pct": _avg(kc_relevance),
            "kc_examples_pct": _avg(kc_examples) if kc_examples else 0.0,
            "kc_terminology_pct": _avg(kc_terminology) if kc_terminology else 0.0,
            "ssf_fluency_pct": _avg(ssf_fluency) if ssf_fluency else _avg([x for x in [_avg(ssf_structure), _avg(ssf_grammar)] if x is not None]),
            "ssf_structure_pct": _avg(ssf_structure),
            "ssf_pacing_pct": _avg(ssf_pacing),
            "ssf_grammar_pct": _avg(ssf_grammar),
            "total_questions": total_questions,
            "attempted_questions": attempted_questions,
            "speech_strengths": _unique(speech_strengths)[:6],
            "speech_improvements": _unique(speech_improvements)[:6],
        }
        
        interview_date = datetime.now(timezone.utc).isoformat()
        llm_data, llm_error, latency_ms, model_name = await synthesize_summary_sections_lite(
            per_question_inputs=per_question_inputs,
            computed_metrics=computed_metrics,
            max_questions=None,
            interview_track=track,
            interview_date=interview_date,
            candidate_name=candidate_name,
            total_questions=total_questions,
        )
        
        if not llm_data or llm_error:
            # Fallback logic for error case
            fallback_std = self._build_fallback_report(
                interview_id=interview_id,
                track=track,
                computed_metrics=computed_metrics,
                all_questions=all_interview_questions,
                attempts_map=attempts_by_question_id,
                interview_date=interview_date,
                candidate_name=candidate_name,
            )
            # Construct minimal valid response
            qa_items = []
            for item in fallback_std.get("questionAnalysis", []):
                qa_items.append(QuestionAnalysisItemLite(
                    id=item["id"],
                    totalQuestions=item["totalQuestions"],
                    type=item["type"],
                    question=item["question"],
                    feedback=QuestionFeedbackLite(strengths="N/A", areasOfImprovement="N/A")
                ))
            
            return SummaryReportResponseLite(
                reportId=str(uuid.uuid4()),
                candidateInfo=CandidateInfoLite(
                    name=candidate_name,
                    interviewDate=interview_date,
                    roleTopic=track.title(),
                    duration=duration_str,
                    durationFeedback=duration_feedback
                ),
                scoreSummary=ScoreSummary(**self._maybe_strip_knowledge_summary(track=track, score_summary=dict(fallback_std["scoreSummary"]))),
                questionAnalysis=qa_items,
                recommendedPractice={"title": "Practice", "description": "Practice more."},
                speechFluencyFeedback={"strengths": "N/A", "areasOfImprovement": "N/A", "ratingEmoji": "Neutral", "ratingTitle": "N/A", "ratingDescription": "N/A"},
                nextSteps=[{"title": "Practice"}],
                finalTip={"title": "Tip", "description": "Keep practicing."}
            ).model_dump(exclude_none=True)

        try:
            final_report = self._calculate_final_scores_lite(
                llm_data=llm_data,
                total_questions=total_questions,
                all_questions=all_interview_questions,
                attempts_by_question_id=attempts_by_question_id,
                actually_attempted_question_ids=actually_attempted_question_ids,
                track=track,
                interview_date=interview_date,
                candidate_name=candidate_name,
                duration_str=duration_str,
                duration_feedback=duration_feedback,
            )
            parsed = SummaryReportResponseLite(**final_report)
            return parsed.model_dump(exclude_none=True)
        except Exception as e:
            # Fallback on exception
            print(f"Error parsing lite report: {e}")
            # Return minimal valid response (same as above)
            return SummaryReportResponseLite(
                reportId=str(uuid.uuid4()),
                candidateInfo=CandidateInfoLite(
                    name=candidate_name,
                    interviewDate=interview_date,
                    roleTopic=track.title(),
                    duration=duration_str,
                    durationFeedback=duration_feedback
                ),
                scoreSummary=ScoreSummary(
                    speechAndStructure=SpeechAndStructureScore(score=0, maxScore=20, average=0, maxAverage=5, percentage=0, criteria=SpeechCriteria())
                ),
                questionAnalysis=[],
                recommendedPractice={"title": "Error", "description": "Could not generate report."},
                speechFluencyFeedback={"strengths": "Error", "areasOfImprovement": "Error", "ratingEmoji": "Sad", "ratingTitle": "Error", "ratingDescription": "Error"},
                nextSteps=[],
                finalTip={"title": "Error", "description": "Error"}
            ).model_dump(exclude_none=True)

    def _calculate_final_scores_lite(
        self,
        llm_data: Dict[str, Any],
        total_questions: int,
        all_questions: List[InterviewQuestion],
        attempts_by_question_id: Dict[int, QuestionAttempt],
        actually_attempted_question_ids: set[int],
        track: str,
        interview_date: str,
        candidate_name: str | None,
        duration_str: str,
        duration_feedback: str,
    ) -> Dict[str, Any]:
        """Calculate final scores for Lite report."""
        per_question_scores = llm_data.get("perQuestionScores", [])
        per_question_feedback = llm_data.get("perQuestionFeedback", [])
        attempted_questions = len(per_question_scores)
        
        kc_accuracy_sum = kc_depth_sum = kc_relevance_sum = kc_examples_sum = kc_terminology_sum = 0
        ssf_fluency_sum = ssf_structure_sum = ssf_pacing_sum = ssf_grammar_sum = 0
        
        for q_scores in per_question_scores:
            k = q_scores.get("knowledgeScores", {})
            kc_accuracy_sum += k.get("accuracy", 0)
            kc_depth_sum += k.get("depth", 0)
            kc_relevance_sum += k.get("relevance", 0)
            kc_examples_sum += k.get("examples", 0)
            kc_terminology_sum += k.get("terminology", 0)
            s = q_scores.get("speechScores", {})
            ssf_fluency_sum += s.get("fluency", 0)
            ssf_structure_sum += s.get("structure", 0)
            ssf_pacing_sum += s.get("pacing", 0)
            ssf_grammar_sum += s.get("grammar", 0)
            
        kc_total_from_attempted = (kc_accuracy_sum + kc_depth_sum + kc_relevance_sum + kc_examples_sum + kc_terminology_sum)
        ssf_total_from_attempted = (ssf_fluency_sum + ssf_structure_sum + ssf_pacing_sum + ssf_grammar_sum)
        
        if attempted_questions > 0:
            kc_avg_per_question = kc_total_from_attempted / attempted_questions
            ssf_avg_per_question = ssf_total_from_attempted / attempted_questions
        else:
            kc_avg_per_question = ssf_avg_per_question = 0
            
        completion_ratio = attempted_questions / total_questions if total_questions > 0 else 0.0
        kc_score_total = round(kc_avg_per_question * completion_ratio)
        ssf_score_total = round(ssf_avg_per_question * completion_ratio)
        
        if kc_total_from_attempted > 0:
            kc_accuracy_final = round(kc_score_total * (kc_accuracy_sum / kc_total_from_attempted))
            kc_depth_final = round(kc_score_total * (kc_depth_sum / kc_total_from_attempted))
            kc_relevance_final = round(kc_score_total * (kc_relevance_sum / kc_total_from_attempted))
            kc_examples_final = round(kc_score_total * (kc_examples_sum / kc_total_from_attempted))
            kc_terminology_final = kc_score_total - (kc_accuracy_final + kc_depth_final + kc_relevance_final + kc_examples_final)
        else:
            kc_accuracy_final = kc_depth_final = kc_relevance_final = kc_examples_final = kc_terminology_final = 0
            
        if ssf_total_from_attempted > 0:
            ssf_fluency_final = round(ssf_score_total * (ssf_fluency_sum / ssf_total_from_attempted))
            ssf_structure_final = round(ssf_score_total * (ssf_structure_sum / ssf_total_from_attempted))
            ssf_pacing_final = round(ssf_score_total * (ssf_pacing_sum / ssf_total_from_attempted))
            ssf_grammar_final = ssf_score_total - (ssf_fluency_final + ssf_structure_final + ssf_pacing_final)
        else:
            ssf_fluency_final = ssf_structure_final = ssf_pacing_final = ssf_grammar_final = 0
            
        kc_avg = kc_score_total / 5.0
        ssf_avg = ssf_score_total / 4.0
        kc_pct = int((kc_score_total / 25.0) * 100)
        ssf_pct = int((ssf_score_total / 20.0) * 100)
        
        score_summary = {
            "knowledgeCompetence": {
                "score": kc_score_total, "maxScore": 25, "average": round(kc_avg, 2), "maxAverage": 5.0, "percentage": kc_pct,
                "criteria": {
                    "accuracy": kc_accuracy_final, "depth": kc_depth_final, "relevance": kc_relevance_final,
                    "examples": kc_examples_final, "terminology": kc_terminology_final
                }
            },
            "speechAndStructure": {
                "score": ssf_score_total, "maxScore": 20, "average": round(ssf_avg, 2), "maxAverage": 5.0, "percentage": ssf_pct,
                "criteria": {
                    "fluency": ssf_fluency_final, "structure": ssf_structure_final, "pacing": ssf_pacing_final, "grammar": ssf_grammar_final
                }
            }
        }
        score_summary = self._maybe_strip_knowledge_summary(track=track, score_summary=score_summary)
        
        candidate_info = {
            "name": candidate_name,
            "interviewDate": interview_date,
            "roleTopic": track.title(),
            "duration": duration_str,
            "durationFeedback": duration_feedback,
        }
        
        question_analysis = []
        feedback_idx = 0
        
        for idx, iq in enumerate(all_questions):
            question_type = _question_type_label(iq.category)
            
            # Check if attempted
            if iq.id not in actually_attempted_question_ids:
                # Not attempted
                feedback_item = {
                    "strengths": "",
                    "areasOfImprovement": "Not attempted"
                }
            else:
                # Attempted - get feedback from LLM
                feedback_item = None
                if feedback_idx < len(per_question_feedback):
                    fb_data = per_question_feedback[feedback_idx]
                    feedback_item = {
                        "strengths": fb_data.get("strengths", ""),
                        "areasOfImprovement": fb_data.get("areasOfImprovement", "")
                    }
                    feedback_idx += 1
            
            question_analysis.append({
                "id": idx + 1,
                "totalQuestions": total_questions,
                "type": question_type,
                "question": iq.text,
                "feedback": feedback_item,
            })
            
        return {
            "reportId": str(uuid.uuid4()),
            "candidateInfo": candidate_info,
            "scoreSummary": score_summary,
            "questionAnalysis": question_analysis,
            "recommendedPractice": llm_data.get("recommendedPractice"),
            "speechFluencyFeedback": llm_data.get("speechFluencyFeedback"),
            "nextSteps": llm_data.get("nextSteps"),
            "finalTip": llm_data.get("finalTip"),
        }
