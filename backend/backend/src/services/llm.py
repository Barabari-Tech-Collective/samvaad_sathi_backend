import json
import random
import time
from typing import Any, Type, List, Dict, Literal, TypeVar, Optional, Union
import logging

import pydantic
from pydantic import Field, AliasChoices
from openai import AsyncOpenAI
from src.config.manager import settings
from src.models.schemas.summary_report import SummarySection, SummarySectionGroup, SummaryMetrics
logger = logging.getLogger(__name__)
# Lazy client holder; create only when needed and when API key is present
_client: AsyncOpenAI | None = None


def get_active_llm_provider() -> str:
    provider = (settings.LLM_PROVIDER or "openai").strip().lower()
    return provider if provider in ("openai", "deepseek") else "openai"


def get_active_llm_model_and_key() -> tuple[str, str]:
    """Model string and API key for whichever provider LLM_PROVIDER selects.
    DeepSeek's API is OpenAI-ChatCompletions-compatible, so the same
    AsyncOpenAI client class works for both - only base_url/api_key/model
    differ (see get_llm_client())."""
    if get_active_llm_provider() == "deepseek":
        return settings.DEEPSEEK_MODEL, settings.DEEPSEEK_API_KEY
    return settings.OPENAI_MODEL, settings.OPENAI_API_KEY


def get_llm_client() -> AsyncOpenAI | None:
    global _client
    if _client is not None:
        return _client

    provider = get_active_llm_provider()
    _, api_key = get_active_llm_model_and_key()
    if not api_key:
        logger.error(
            "%s_API_KEY is missing; LLM client cannot be initialized",
            provider.upper(),
        )
        return None

    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": float(getattr(settings, "OPENAI_TIMEOUT_SECONDS", 60.0)),
        "max_retries": 3,
    }
    if provider == "deepseek":
        client_kwargs["base_url"] = settings.DEEPSEEK_BASE_URL

    logger.info(
        "Initializing %s AsyncClient model=%s timeout=%s max_retries=3",
        provider,
        settings.DEEPSEEK_MODEL if provider == "deepseek" else settings.OPENAI_MODEL,
        client_kwargs["timeout"],
    )
    _client = AsyncOpenAI(**client_kwargs)
    logger.info("%s AsyncClient initialized successfully", provider)

    return _client


class ResumeEntitiesLLM(pydantic.BaseModel):
    skills: list[str] = pydantic.Field(default_factory=list)
    years_experience: float | None = None


class JobDescriptionSkillsLLM(pydantic.BaseModel):
    skills: list[str] = pydantic.Field(default_factory=list)


# Base class for items with date ranges
class BaseDateRangeItemLLM(pydantic.BaseModel):
    """Base class for items with start/end dates."""
    start_date: str | None = None
    end_date: str | None = None


class EducationItemLLM(BaseDateRangeItemLLM):
    """Education item with degree and institution."""
    degree: str | None = None
    institution: str | None = None


class ExperienceItemLLM(BaseDateRangeItemLLM):
    """Experience item with company, role, and related data."""
    company: str | None = None
    role: str | None = None
    responsibilities: list[str] | None = None
    technologies: list[str] | None = None


class ProjectItemLLM(pydantic.BaseModel):
    """Project item with name, description, and technologies."""
    name: str | None = None
    description: str | None = None
    technologies: list[str] | None = None
    link: str | None = None


class ResumeEntitiesV2LLM(pydantic.BaseModel):
    # Backward-compatible core
    skills: list[str] = pydantic.Field(default_factory=list)
    years_experience: float | None = None
    # Additional details
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: list[str] | None = None
    summary: str | None = None
    education: list[EducationItemLLM] | None = None
    experience: list[ExperienceItemLLM] | None = None
    projects: list[ProjectItemLLM] | None = None
    certifications: list[str] | None = None
    languages: list[str] | None = None
    job_titles: list[str] | None = None
    companies: list[str] | None = None
    llm_schema: str | None = None


# New structured output models for restructured summary report
class LLMCandidateInfo(pydantic.BaseModel):
    """Candidate and interview information."""
    name: str | None = None
    interviewDate: str
    roleTopic: str


# Per-question score models (LLM calculates these independently)
class LLMQuestionKnowledgeScores(pydantic.BaseModel):
    """Knowledge scores for a single question (each criterion 0-5)."""
    accuracy: int = pydantic.Field(..., ge=0, le=5)
    depth: int = pydantic.Field(..., ge=0, le=5)
    relevance: int = pydantic.Field(..., ge=0, le=5)
    examples: int = pydantic.Field(..., ge=0, le=5)
    terminology: int = pydantic.Field(..., ge=0, le=5)


class LLMQuestionSpeechScores(pydantic.BaseModel):
    """Speech scores for a single question (each criterion 0-5)."""
    fluency: int = pydantic.Field(..., ge=0, le=5)
    structure: int = pydantic.Field(..., ge=0, le=5)
    pacing: int = pydantic.Field(..., ge=0, le=5)
    grammar: int = pydantic.Field(..., ge=0, le=5)


class LLMQuestionScores(pydantic.BaseModel):
    """Combined scores for a single question."""
    questionId: int
    knowledgeScores: LLMQuestionKnowledgeScores
    speechScores: LLMQuestionSpeechScores


class LLMActionableStep(pydantic.BaseModel):
    """Individual actionable step with title and description."""
    title: str
    description: str


class LLMSpeechFluencyFeedback(pydantic.BaseModel):
    """Speech fluency feedback section."""
    strengths: list[str] = pydantic.Field(default_factory=list)
    areasOfImprovement: list[str] = pydantic.Field(default_factory=list)
    actionableSteps: list[LLMActionableStep] = pydantic.Field(default_factory=list)


class LLMOverallFeedback(pydantic.BaseModel):
    """Overall feedback containing speech fluency."""
    speechFluency: LLMSpeechFluencyFeedback


class LLMQuestionFeedbackSubsection(pydantic.BaseModel):
    """Knowledge-related feedback subsection."""
    strengths: list[str] = pydantic.Field(default_factory=list)
    areasOfImprovement: list[str] = pydantic.Field(default_factory=list)
    actionableInsights: list[LLMActionableStep] = pydantic.Field(default_factory=list)


class LLMQuestionFeedback(pydantic.BaseModel):
    """Complete feedback for a single question."""
    knowledgeRelated: LLMQuestionFeedbackSubsection


class LLMQuestionAnalysisItem(pydantic.BaseModel):
    """Individual question analysis."""
    id: int
    totalQuestions: int
    type: str
    question: str
    feedback: LLMQuestionFeedback | None = None


class FollowUpQuestionLLM(pydantic.BaseModel):
    """Structured output for adaptive follow-up question generation."""
    question: str = pydantic.Field(..., min_length=4)


class LLMSupplementItem(pydantic.BaseModel):
    """Structured supplement payload for a question."""
    questionId: int
    supplementType: str = pydantic.Field(pattern="^(code|diagram)$")
    format: str | None = None
    content: str


class LLMSupplementResponse(pydantic.BaseModel):
    items: list[LLMSupplementItem] = pydantic.Field(default_factory=list)


class NewStrictSummarySynthesisLLM(pydantic.BaseModel):
    """Restructured summary report output - LLM provides only scores and feedback, code handles metadata."""
    perQuestionScores: list[LLMQuestionScores]  # LLM scores each attempted question individually
    overallFeedback: LLMOverallFeedback
    perQuestionFeedback: list[LLMQuestionFeedback]  # Feedback per attempted question (same length as perQuestionScores)


class LLMQuestionFeedbackLite(pydantic.BaseModel):
    """Simplified feedback for a single question."""
    strengths: str
    areasOfImprovement: str

class LLMNextStepStrict(pydantic.BaseModel):
    """Individual next step object expected by schema."""
    title: str


class LLMRecommendedPracticeStrict(pydantic.BaseModel):
    title: str
    description: str


class LLMSpeechFluencyFeedbackLiteStrict(pydantic.BaseModel):
    strengths: str
    areasOfImprovement: str
    ratingEmoji: str
    ratingTitle: str
    ratingDescription: str

class LLMFinalTipStrict(pydantic.BaseModel):
    title: str
    description: str

class NewStrictSummarySynthesisLLMLite(pydantic.BaseModel):
    """Restructured summary report output (Lite) - LLM provides only scores and simplified feedback."""
    perQuestionScores: list[LLMQuestionScores]
    overallFeedback: LLMOverallFeedback
    perQuestionFeedback: list[LLMQuestionFeedbackLite]
    recommendedPractice: LLMRecommendedPracticeStrict | None = None
    speechFluencyFeedback: LLMSpeechFluencyFeedbackLiteStrict | None = None
    nextSteps: list[Union[LLMNextStepStrict, str]] = pydantic.Field(default_factory=list)
    finalTip: LLMFinalTipStrict | None = None

    @pydantic.field_validator("nextSteps", mode="after")
    @classmethod
    def normalize_next_steps(cls, v: list[Any] | None) -> list[LLMNextStepStrict]:
        """Automatically converts raw strings like ['Review Node.js'] into [{'title': 'Review Node.js'}]"""
        if not v:
            return []
        normalized = []
        for item in v:
            if isinstance(item, str):
                normalized.append(LLMNextStepStrict(title=item))
            elif isinstance(item, dict):
                normalized.append(LLMNextStepStrict(**item))
            elif isinstance(item, LLMNextStepStrict):
                normalized.append(item)
        return normalized


# Legacy models (deprecated - kept for backward compatibility)
class LLMKnowledgeBreakdownStrict(pydantic.BaseModel):
    accuracy: float = pydantic.Field(..., ge=0.0, le=5.0)
    depth: float = pydantic.Field(..., ge=0.0, le=5.0)
    coverage: float = pydantic.Field(..., ge=0.0, le=5.0)
    relevance: float = pydantic.Field(..., ge=0.0, le=5.0)


class LLMSpeechBreakdownStrict(pydantic.BaseModel):
    pacing: float = pydantic.Field(..., ge=0.0, le=5.0)
    structure: float = pydantic.Field(..., ge=0.0, le=5.0)
    pauses: float = pydantic.Field(..., ge=0.0, le=5.0)
    grammar: float = pydantic.Field(..., ge=0.0, le=5.0)


class LLMKnowledgeCompetenceStrict(pydantic.BaseModel):
    average5pt: float = pydantic.Field(..., ge=0.0, le=5.0)
    averagePct: float = pydantic.Field(..., ge=0.0, le=100.0)
    breakdown: LLMKnowledgeBreakdownStrict


class LLMSpeechStructureStrict(pydantic.BaseModel):
    average5pt: float = pydantic.Field(..., ge=0.0, le=5.0)
    averagePct: float = pydantic.Field(..., ge=0.0, le=100.0)
    breakdown: LLMSpeechBreakdownStrict


class LLMOverallScoreSummaryStrict(pydantic.BaseModel):
    knowledgeCompetence: LLMKnowledgeCompetenceStrict
    speechStructure: LLMSpeechStructureStrict


class LLMSectionGroupStrict(pydantic.BaseModel):
    label: str
    items: list[str] = pydantic.Field(default_factory=list)


class LLMSectionStrict(pydantic.BaseModel):
    heading: str
    subtitle: str | None = None
    groups: list[LLMSectionGroupStrict] = pydantic.Field(default_factory=list)


class LLMPerQuestionItemStrict(pydantic.BaseModel):
    questionAttemptId: int
    questionText: str | None = None
    keyTakeaways: list[str] = pydantic.Field(default_factory=list)
    knowledgeScorePct: float = pydantic.Field(..., ge=0.0, le=100.0)
    speechScorePct: float = pydantic.Field(..., ge=0.0, le=100.0)


class LLMTopicHighlightsStrict(pydantic.BaseModel):
    strengthsTopics: list[str] = pydantic.Field(default_factory=list)
    improvementTopics: list[str] = pydantic.Field(default_factory=list)


class StrictSummarySynthesisLLM(pydantic.BaseModel):
    """Strict, non-optional output for UI-style summary synthesis (no metadata)."""
    metrics: LLMOverallScoreSummaryStrict
    strengths: LLMSectionStrict
    areasOfImprovement: LLMSectionStrict
    actionableInsights: LLMSectionStrict
    perQuestion: list[LLMPerQuestionItemStrict] | None = None
    topicHighlights: LLMTopicHighlightsStrict | None = None


async def synthesize_summary_sections(
    *,
    per_question_inputs: List[dict],
    computed_metrics: Dict[str, Any],
    max_questions: int | None = None,
    interview_track: str | None = None,
    interview_date: str | None = None,
    candidate_name: str | None = None,
    total_questions: int = 0,
) -> tuple[dict, str | None, int | None, str]:
    """
    Drive the LLM to create the restructured summary report from per-question analyses.
    Returns: (summary_json, error, latency_ms, model)
    """
    logger.info(
        "LLM summary synthesis START total_questions=%s input_count=%s max_questions=%s model=%s",
        total_questions,
        len(per_question_inputs),
        max_questions,
        settings.OPENAI_MODEL,
    )
    model = settings.OPENAI_MODEL
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        logger.error(
            "LLM summary synthesis ABORTED: OPENAI_API_KEY missing"
        )
        # No key: return empty structures; caller can fallback to heuristic
        return {}, None, None, model

    if max_questions is not None:
        original_count = len(per_question_inputs)
        per_question_inputs = per_question_inputs[:max_questions]
        logger.info(
            "LLM summary inputs truncated original_count=%s final_count=%s max_questions=%s",
            original_count,
            len(per_question_inputs),
            max_questions,
        )

    sys_prompt = (
        "You are an expert technical interview coach. Given per-question analyses (domain, communication, pace, "
        "pause) for interview questions, analyze each question independently and provide scores and feedback.\n\n"
        "Your task: \n"
        "1. Score each attempted question on knowledge and speech criteria (0-5 scale per criterion)\n"
        "2. Provide overall speech fluency feedback across all attempts\n"
        "3. Provide per-question knowledge feedback for each attempted question\n\n"
        "The code will handle: reportId, candidateInfo, question metadata, totals, averages, and percentages.\n\n"
        "Strict JSON schema: {\n"
        "  perQuestionScores: [{ questionId: int, knowledgeScores: { accuracy: int(0..5), depth: int(0..5), relevance: int(0..5), examples: int(0..5), terminology: int(0..5) }, speechScores: { fluency: int(0..5), structure: int(0..5), pacing: int(0..5), grammar: int(0..5) } }],\n"
        "  overallFeedback: { speechFluency: { strengths: string[], areasOfImprovement: string[], actionableSteps: [{ title: string, description: string }] } },\n"
        "  perQuestionFeedback: [{ knowledgeRelated: { strengths: string[], areasOfImprovement: string[], actionableInsights: [{ title: string, description: string }] } } | null]\n"
        "}\n\n"
        "SCORING GUIDELINES:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "PER-QUESTION SCORING (0-5 scale for each criterion):\n"
        "\n"
        "Knowledge Criteria:\n"
        "  - accuracy: How correct and factually accurate was the answer?\n"
        "  - depth: How detailed and comprehensive was the explanation?\n"
        "  - relevance: How well did the answer address the question?\n"
        "  - examples: Quality and appropriateness of examples provided\n"
        "  - terminology: Proper use of technical terms and concepts\n"
        "\n"
        "Speech Criteria:\n"
        "  - fluency: Smoothness of speech, minimal hesitations/filler words\n"
        "  - structure: Logical organization and clarity of response\n"
        "  - pacing: Appropriate speech speed (not too fast/slow)\n"
        "  - grammar: Correct sentence structure and language use\n"
        "\n"
        "CRITICAL - DETECTING NON-ANSWERS:\n"
        "⚠️  If the candidate's transcription shows they did NOT provide a real answer, treat it as unattempted:\n"
        "   - Responses like 'I don't know', 'I'm not sure', 'pass', 'skip', or very short non-answers (< 10 words)\n"
        "   - In these cases: Give ALL knowledge scores as 0, keep speech scores reasonable if they spoke\n"
        "   - In feedback, state: 'No substantial answer provided' or 'Question not answered'\n"
        "   - DO NOT hallucinate feedback about content that wasn't provided\n"
        "   - DO NOT give positive feedback if there was no real attempt at answering\n"
        "\n"
        "CRITICAL - ACTIONABLE GUIDANCE FOR WRONG OR WEAK ANSWERS:\n"
        "⚠️  Whenever an answer is incorrect, incomplete, or off-target, areas of improvement must go beyond\n"
        "   naming what was wrong — they must also say what the candidate should have said or done instead.\n"
        "   Do not stop at a diagnosis like 'lacked specific examples' — follow it with the concrete fix, e.g.\n"
        "   'lacked specific examples — you could have mentioned a project where you used X'. The candidate\n"
        "   should be able to read the feedback and understand exactly what a stronger answer would have\n"
        "   included, not just that their answer fell short.\n"
        "\n"
        "IMPORTANT NOTES:\n"
        "1. perQuestionScores: Include scores for ALL questions provided in per_question data\n"
        "2. perQuestionFeedback: Array corresponding to perQuestionScores order (same length)\n"
        "   - Each entry must have SPECIFIC, NON-EMPTY feedback based on the candidate's actual response\n"
        "   - Include 2-3 specific strengths (what they did well) - ONLY if they actually attempted the question\n"
        "   - Include 2-3 specific areas of improvement: what was missing or weak, AND what the candidate\n"
        "     should have said/done instead (see 'ACTIONABLE GUIDANCE' above)\n"
        "   - Include 3-4 actionable insights with clear titles and detailed descriptions\n"
        "3. Base scores on the computed_metrics and analysis data provided for each question\n"
        "4. Each criterion is scored independently on 0-5 scale\n"
        "5. DO NOT calculate totals, averages, or percentages - code will do this\n"
        "6. overallFeedback.speechFluency: Focus ONLY on speech aspects across all attempts (3-4 actionable steps)\n"
        "   CRITICAL: if most attempts had zero/near-zero pace or pause scores in the provided data (meaning most\n"
        "   answers were empty or near-empty), do NOT write confident, specific-sounding pacing/fluency commentary\n"
        "   like 'good strategic pauses' — those numbers reflect negligible speech, not real delivery. Say plainly\n"
        "   there wasn't enough spoken content across the interview to assess fluency instead.\n"
        "7. DO NOT return empty arrays - every question MUST have meaningful, specific feedback\n"
        "8. Keep language simple and actionable - avoid jargon like 'WPM'\n"
        "9. These were all oral interviews so your recommendations should not be about things like writing code\n"
        "10. NEVER provide positive feedback if the candidate said 'I don't know' or gave a non-answer"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    user_content = {
        "per_question": per_question_inputs,
        "computed_metrics": computed_metrics,
        "total_questions": total_questions,
        "guidelines": [
            "Base all numeric scores on provided computed_metrics and per-question analyses",
            "Focus overallFeedback.speechFluency ONLY on speech aspects, not knowledge",
            "Provide specific, actionable feedback grounded in observed patterns",
            "Use simple language; avoid jargon like 'WPM' or overly technical terms",
            "Connect improvement suggestions to specific weaknesses observed in analyses",
            "Ensure actionableSteps have clear titles and detailed descriptions"
        ],
    }
    
    logger.info(
        "Calling structured_output for summary synthesis "
        "schema=%s input_count=%s computed_metrics_keys=%s",
        NewStrictSummarySynthesisLLM.__name__,
        len(per_question_inputs),
        list(computed_metrics.keys()) if isinstance(computed_metrics, dict) else type(computed_metrics).__name__,
    )

    logger.debug(
        "Summary synthesis input question IDs=%s",
        [
            item.get("questionId") or item.get("question_id") or item.get("id")
            for item in per_question_inputs
            if isinstance(item, dict)
        ],
    )
    result, error, latency_ms, model = await structured_output(
        NewStrictSummarySynthesisLLM,
        system_prompt=sys_prompt,
        user_content=user_content,
        temperature=0,
    )

    logger.info(
        "structured_output returned for summary synthesis "
        "result_present=%s error_present=%s latency_ms=%s model=%s",
        result is not None,
        bool(error),
        latency_ms,
        model,
    )
    if error:
        logger.error(
            "Summary synthesis LLM error: %s",
            error,
        )

    data: dict = {}
    if result:
        logger.info(
            "Summary synthesis Pydantic result validated successfully schema=%s",
            type(result).__name__,
        )

        data = result.model_dump()

        logger.debug(
            "Summary synthesis result keys=%s",
            list(data.keys()),
        )
    else:
        logger.error(
            "Summary synthesis returned NO validated result error=%s",
            error,
        )

    return data, error, latency_ms, model


class LLMKnowledgeScoresStrict(pydantic.BaseModel):
    accuracy: int = pydantic.Field(..., ge=0, le=5)
    depth: int = pydantic.Field(..., ge=0, le=5)
    relevance: int = pydantic.Field(..., ge=0, le=5)
    examples: int = pydantic.Field(..., ge=0, le=5)
    terminology: int = pydantic.Field(..., ge=0, le=5)


class LLMSpeechScoresStrict(pydantic.BaseModel):
    fluency: int = pydantic.Field(..., ge=0, le=5)
    structure: int = pydantic.Field(..., ge=0, le=5)
    pacing: int = pydantic.Field(..., ge=0, le=5)
    grammar: int = pydantic.Field(..., ge=0, le=5)


class LLMPerQuestionScoresStrict(pydantic.BaseModel):
    questionId: int
    knowledgeScores: LLMKnowledgeScoresStrict
    speechScores: LLMSpeechScoresStrict


class LLMQuestionFeedbackLiteStrict(pydantic.BaseModel):
    strengths: str
    areasOfImprovement: str


class LLMRecommendedPracticeStrict(pydantic.BaseModel):
    title: str
    description: str


class LLMSpeechFluencyFeedbackStrict(pydantic.BaseModel):
    strengths: str
    areasOfImprovement: str
    ratingEmoji: Literal['Excellent', 'Good', 'Average', 'Needs-Improvement', 'Poor']
    ratingTitle: str
    ratingDescription: str


class LLMNextStepStrict(pydantic.BaseModel):
    title: str


class LLMFinalTipStrict(pydantic.BaseModel):
    title: str
    description: str


class NewStrictSummarySynthesisLLMLite(pydantic.BaseModel):
    perQuestionScores: list[LLMPerQuestionScoresStrict] = pydantic.Field(default_factory=list)
    perQuestionFeedback: list[LLMQuestionFeedbackLiteStrict] = pydantic.Field(default_factory=list)
    recommendedPractice: LLMRecommendedPracticeStrict | None = None
    speechFluencyFeedback: LLMSpeechFluencyFeedbackStrict | None = None
    nextSteps: list[LLMNextStepStrict] = pydantic.Field(default_factory=list)
    finalTip: LLMFinalTipStrict | None = None
    # perQuestionScores: list[LLMPerQuestionScoresStrict]
    # perQuestionFeedback: list[LLMQuestionFeedbackLiteStrict]
    # recommendedPractice: LLMRecommendedPracticeStrict
    # speechFluencyFeedback: LLMSpeechFluencyFeedbackStrict
    # nextSteps: list[LLMNextStepStrict]
    # finalTip: LLMFinalTipStrict


async def synthesize_summary_sections_lite(
    *,
    per_question_inputs: List[dict],
    computed_metrics: Dict[str, Any],
    max_questions: int | None = None,
    interview_track: str | None = None,
    interview_date: str | None = None,
    candidate_name: str | None = None,
    total_questions: int = 0,
) -> tuple[dict, str | None, int | None, str]:
    """
    Drive the LLM to create the restructured summary report (Lite) from per-question analyses.
    Returns: (summary_json, error, latency_ms, model)
    """
    logger.info(
        "LLM LITE summary synthesis START total_questions=%s input_count=%s max_questions=%s model=%s",
        total_questions,
        len(per_question_inputs),
        max_questions,
        settings.OPENAI_MODEL,
    )
    model = settings.OPENAI_MODEL
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        # No key: return empty structures; caller can fallback to heuristic
        return {}, None, None, model

    if max_questions is not None:
        per_question_inputs = per_question_inputs[:max_questions]

    sys_prompt = (
        "You are an expert technical interview coach. Given per-question analyses (domain, communication, pace, "
        "pause) for interview questions, analyze each question independently and provide scores and feedback.\n\n"
        "Your task: \n"
        "1. Score each attempted question on knowledge and speech criteria (0-5 scale per criterion)\n"
        "2. Provide overall speech fluency feedback across all attempts\n"
        "3. Provide per-question simplified feedback for each attempted question\n"
        "4. Provide a recommended practice exercise\n"
        "5. Provide immediate next steps\n"
        "6. Provide a final tip\n\n"
        "The code will handle: reportId, candidateInfo, question metadata, totals, averages, and percentages.\n\n"
        "Strict JSON schema: {\n"
        "  perQuestionScores: [{ questionId: int, knowledgeScores: { accuracy: int(0..5), depth: int(0..5), relevance: int(0..5), examples: int(0..5), terminology: int(0..5) }, speechScores: { fluency: int(0..5), structure: int(0..5), pacing: int(0..5), grammar: int(0..5) } }],\n"
        "  perQuestionFeedback: [{ strengths: string, areasOfImprovement: string }],\n"
        "  recommendedPractice: { title: string, description: string },\n"
        "  speechFluencyFeedback: { strengths: string, areasOfImprovement: string, ratingEmoji: string, ratingTitle: string, ratingDescription: string },\n"
        "  nextSteps: [{ title: string }],\n"
        "  finalTip: { title: string, description: string }\n"
        "}\n\n"
        "SCORING GUIDELINES:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "PER-QUESTION SCORING (0-5 scale for each criterion):\n"
        "\n"
        "Knowledge Criteria:\n"
        "  - accuracy: How correct and factually accurate was the answer?\n"
        "  - depth: How detailed and comprehensive was the explanation?\n"
        "  - relevance: How well did the answer address the question?\n"
        "  - examples: Quality and appropriateness of examples provided\n"
        "  - terminology: Proper use of technical terms and concepts\n"
        "\n"
        "Speech Criteria:\n"
        "  - fluency: Smoothness of speech, minimal hesitations/filler words\n"
        "  - structure: Logical organization and clarity of response\n"
        "  - pacing: Appropriate speech speed (not too fast/slow)\n"
        "  - grammar: Correct sentence structure and language use\n"
        "\n"
        "CRITICAL - DETECTING NON-ANSWERS:\n"
        "⚠️  If the candidate's transcription shows they did NOT provide a real answer, treat it as unattempted:\n"
        "   - Responses like 'I don't know', 'I'm not sure', 'pass', 'skip', or very short non-answers (< 10 words)\n"
        "   - In these cases: Give ALL knowledge scores as 0, keep speech scores reasonable if they spoke\n"
        "   - In feedback strengths: Leave empty or say 'None identified'\n"
        "   - In feedback areasOfImprovement: State 'No substantial answer provided' or 'Question not answered'\n"
        "   - DO NOT hallucinate feedback about content that wasn't provided\n"
        "   - DO NOT give positive feedback if there was no real attempt at answering\n"
        "\n"
        "CRITICAL - PROPORTIONAL FEEDBACK FOR BRIEF ANSWERS:\n"
        "⚠️  If the candidate DID give a real, on-topic answer but it was very brief or unelaborated (roughly 10-20\n"
        "   words, more than a non-answer but far short of a full response), your feedback must say so explicitly:\n"
        "   - Do NOT default to generic developmental coaching (e.g. 'develop a structured routine', 'add more\n"
        "     examples') as if a fuller answer had been given — that reads as templated and ignores what was\n"
        "     actually said\n"
        "   - Instead, name the brevity directly, e.g. 'Your answer was very brief and didn't elaborate on X' or\n"
        "     'You gave a one-line response — try expanding with specific detail next time'\n"
        "   - Only give the standard full-length coaching feedback (structure, depth, examples) when the answer\n"
        "     was actually long/substantial enough to have those qualities assessed\n"
        "\n"
        "CRITICAL - ACTIONABLE GUIDANCE FOR WRONG OR WEAK ANSWERS:\n"
        "⚠️  Whenever an answer is incorrect, incomplete, or off-target (this includes answers scored low on\n"
        "   accuracy/depth/relevance, not just brief ones), areasOfImprovement must go beyond naming what was\n"
        "   wrong — it must also say what the candidate should have said or done instead:\n"
        "   - Do not stop at a diagnosis like 'lacked specific examples' or 'was factually incorrect' — follow it\n"
        "     with the concrete fix, e.g. 'lacked specific examples — you could have mentioned a project where you\n"
        "     used X' or 'was factually incorrect — closures actually retain access to their enclosing scope, not\n"
        "     the global scope'\n"
        "   - The candidate should be able to read areasOfImprovement and understand exactly what a stronger\n"
        "     answer would have included, not just that their answer fell short\n"
        "   - Keep it to one or two concise sentences — this is still meant to be scannable, not an essay\n"
        "\n"
        "IMPORTANT NOTES:\n"
        "1. perQuestionScores: Include scores for ALL questions provided in per_question data\n"
        "2. perQuestionFeedback: Array corresponding to perQuestionScores order (same length)\n"
        "   - Each entry must have SPECIFIC, NON-EMPTY feedback based on the candidate's actual response\n"
        "   - strengths: A SINGLE concise sentence summarizing what they did well (or 'None identified' if no answer).\n"
        "   - areasOfImprovement: What was missing or weak, AND what the candidate should have said/done instead\n"
        "     (see 'ACTIONABLE GUIDANCE' above) — a diagnosis alone is not sufficient.\n"
        "3. Base scores on the computed_metrics and analysis data provided for each question\n"
        "4. Each criterion is scored independently on 0-5 scale\n"
        "5. DO NOT calculate totals, averages, or percentages - code will do this\n"
        "6. speechFluencyFeedback: Focus ONLY on speech aspects across all attempts. ratingEmoji must be EXACTLY one of: 'Excellent', 'Good', 'Average', 'Needs-Improvement', 'Poor'\n"
        "   CRITICAL: speechFluencyFeedback is about the OVERALL interview, not one lucky question — if most\n"
        "   attempts had zero/near-zero pace or pause scores in the provided data (meaning most answers were\n"
        "   empty or near-empty), do NOT write confident, specific-sounding pacing/fluency commentary like\n"
        "   'good strategic pauses' or 'pacing is often too slow' — those numbers reflect negligible speech, not\n"
        "   real delivery, so instead say plainly that there wasn't enough spoken content across the interview to\n"
        "   assess fluency, and ratingEmoji/ratingTitle should reflect that (e.g. 'Needs-Improvement'), not a\n"
        "   plausible-sounding average rating.\n"
        "7. DO NOT return empty arrays - every question MUST have meaningful, specific feedback\n"
        "8. Keep language simple and actionable - avoid jargon like 'WPM'\n"
        "9. These were all oral interviews so your recommendations should not be about things like writing code\n"
        "10. nextSteps: Provide 2-3 immediate next steps (titles only)\n"
        "11. finalTip: A concluding tip for the candidate\n"
        "12. NEVER provide positive feedback if the candidate said 'I don't know' or gave a non-answer"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    "You are an expert technical interview evaluator and coach for a {track} interview.\n"
    "Your job is NOT merely to score the candidate. Your primary goal is to turn the candidate's "
    "actual spoken response into accurate, specific, evidence-based, and educational feedback that "
    "helps the candidate understand exactly what they need to improve.\n\n"

    "============================================================\n"
    "CORE EVALUATION PRINCIPLE\n"
    "============================================================\n"
    "Evaluate what the candidate ACTUALLY said. Do not invent technical knowledge, examples, "
    "strengths, mistakes, or explanations that are not supported by the candidate's response.\n\n"

    "For every question, mentally follow this evaluation process:\n"
    "1. Understand what the question is testing.\n"
    "2. Identify the key technical concepts a strong answer should contain.\n"
    "3. Identify the technical claims and concepts actually present in the candidate's response.\n"
    "4. Compare the candidate's response with the concepts required by the question.\n"
    "5. Identify what was correct, partially correct, incorrect, missing, or insufficiently explained.\n"
    "6. Convert those findings into specific and actionable coaching feedback.\n\n"

    "Do NOT simply summarize the candidate's response. The feedback must explain what the candidate "
    "should understand, explain, or practice to give a stronger answer next time.\n\n"

    "============================================================\n"
    "RESPONSE CLASSIFICATION\n"
    "============================================================\n"
    "Do NOT treat every short or uncertain response as 'unattempted'.\n\n"

    "A. SUBSTANTIVE ANSWER:\n"
    "The candidate provides meaningful technical content that can be evaluated. "
    "Evaluate the technical accuracy, depth, relevance, examples, terminology, and speech normally.\n\n"

    "B. PARTIAL OR UNCERTAIN ANSWER:\n"
    "The candidate provides some technical information but is incomplete, uncertain, or says things "
    "such as 'I think', 'I'm not sure', or 'I don't know much about this'. "
    "Evaluate the knowledge that is actually demonstrated. Clearly identify what is correct and what "
    "technical concepts or depth are missing.\n\n"

    "C. KNOWLEDGE-GAP RESPONSE:\n"
    "If the candidate says things such as 'I don't know', 'I am not sure', or indicates that they "
    "do not know the topic, DO NOT label the response as 'Unattempted' or simply say 'Question not answered'. "
    "Treat this as evidence of a knowledge gap.\n"
    "Do not invent strengths or technical content that was not provided. Instead, explain what important "
    "concepts the candidate should learn in order to answer this question in a future interview.\n"
    "Knowledge scores may be low because the candidate did not demonstrate the required knowledge, "
    "but the feedback must still be educational and useful.\n\n"

    "D. NO USABLE RESPONSE:\n"
    "Only treat a response as not evaluable when there is genuinely no usable candidate response, "
    "such as an empty transcription, silence, unintelligible transcription, or an explicit 'pass'/'skip'.\n"
    "Do not fabricate technical feedback when there is no content to evaluate.\n\n"

    "IMPORTANT:\n"
    "The existence of a transcription alone does not prove technical knowledge. "
    "However, a transcription containing 'I don't know' is still a meaningful response and should be "
    "handled as a knowledge gap rather than incorrectly described as an unattempted interview question.\n\n"

    "============================================================\n"
    "TECHNICAL FEEDBACK REQUIREMENTS\n"
    "============================================================\n"
    "For every question, feedback must be grounded in the actual candidate response.\n\n"

    "STRENGTHS:\n"
    "- Mention specific technical concepts, facts, reasoning, examples, or terminology that the candidate "
    "actually demonstrated correctly.\n"
    "- Do not write generic praise such as 'Good answer', 'Good understanding', or 'You explained it well'.\n"
    "- If the candidate did not demonstrate any meaningful technical strength, do NOT invent one. "
    "You may state that no specific technical strength could be established from the response.\n"
    "- For a knowledge-gap response such as 'I don't know', do not manufacture technical strengths.\n\n"

    "AREAS OF IMPROVEMENT:\n"
    "- Identify the exact technical gap, incorrect assumption, missing concept, weak explanation, or "
    "lack of depth found in the response.\n"
    "- Explain what the candidate should have explained or understood.\n"
    "- Where appropriate, explain the correct technical concept briefly so the feedback teaches the candidate.\n"
    "- Connect the weakness to a concrete learning or practice action.\n"
    "- Avoid vague statements such as 'improve your technical knowledge', 'add more detail', "
    "'practice more', or 'work on your concepts' unless the feedback immediately specifies WHICH concepts "
    "and HOW to improve them.\n\n"

    "A strong improvement statement should answer:\n"
    "WHAT was missing or incorrect?\n"
    "WHY is it important?\n"
    "WHAT should the candidate learn or practice?\n\n"

    "Example of BAD feedback:\n"
    "'You need to improve your knowledge of JavaScript.'\n\n"

    "Example of GOOD feedback:\n"
    "'Your response did not explain how the JavaScript event loop coordinates the call stack, "
    "task queue, and microtask queue. You should revise how synchronous code, Promises, and callbacks "
    "are scheduled, and practice explaining the execution order with a simple example.'\n\n"

    "============================================================\n"
    "ANTI-HALLUCINATION RULES\n"
    "============================================================\n"
    "1. Never invent something the candidate said.\n"
    "2. Never claim that the candidate mentioned a concept unless it appears in the provided response.\n"
    "3. Never invent examples supposedly given by the candidate.\n"
    "4. Never assume that the candidate understands a concept merely because the question belongs to "
    "their technical track.\n"
    "5. Never infer technical knowledge from speech metrics such as fluency, pacing, pauses, grammar, "
    "or communication scores.\n"
    "6. Never use metadata, domain labels, or precomputed speech scores as evidence that the candidate "
    "knows a technical concept.\n"
    "7. If the response does not provide enough evidence to evaluate a criterion, score conservatively "
    "rather than inventing evidence.\n\n"

    "============================================================\n"
    "SCORING RULES\n"
    "============================================================\n"
    "Score each criterion independently from 0 to 5.\n\n"

    "KNOWLEDGE:\n"
    "- accuracy: How factually correct are the technical claims made by the candidate?\n"
    "- depth: How thoroughly does the candidate explain the concept?\n"
    "- relevance: How directly does the response answer the question?\n"
    "- examples: How useful and technically appropriate are the examples provided by the candidate?\n"
    "- terminology: How correctly does the candidate use relevant technical terms?\n\n"

    "SPEECH:\n"
    "- fluency: Smoothness of spoken delivery and unnecessary hesitation/filler usage.\n"
    "- structure: Logical organization and clarity of the spoken answer.\n"
    "- pacing: Whether the speaking speed is appropriate and understandable.\n"
    "- grammar: Sentence construction and language clarity.\n\n"

    "IMPORTANT SCORING PRIORITY:\n"
    "For KNOWLEDGE scores, prioritize the candidate's actual technical response and the requirements "
    "of the question. Do not let unrelated precomputed scores override clear evidence in the response.\n"
    "For SPEECH scores, use the provided speech/communication analysis and the observable characteristics "
    "of the candidate's spoken response.\n\n"

    "A candidate saying 'I don't know' should not receive fabricated technical credit. "
    "However, their feedback must explain what they should learn rather than simply saying "
    "'Question not answered'.\n\n"

    "============================================================\n"
    "SPEECH FEEDBACK\n"
    "============================================================\n"
    "speechFluencyFeedback must focus ONLY on spoken communication.\n"
    "Do not discuss technical knowledge inside speechFluencyFeedback.\n"
    "Discuss patterns such as hesitation, sentence structure, clarity, pacing, fillers, or organization "
    "only when supported by the provided speech analysis.\n\n"

    "============================================================\n"
    "OUTPUT QUALITY REQUIREMENTS\n"
    "============================================================\n"
    "Do not produce generic one-sentence feedback.\n"
    "For each question, provide useful and specific feedback in 2-3 sentences where enough evidence exists.\n"
    "The number of sentences is less important than the quality and specificity of the information.\n\n"

    "Each per-question 'strengths' response should explain concrete evidence of what the candidate did well, "
    "not generic praise.\n\n"

    "Each per-question 'areasOfImprovement' response should identify concrete technical gaps and explain "
    "what the candidate should learn or practice.\n\n"

    "If the candidate gave a knowledge-gap response such as 'I don't know', the improvement feedback should "
    "teach the candidate the key concepts required to answer the question rather than merely stating that "
    "they did not answer it.\n\n"

    "Do not use jargon that is unnecessary for the candidate. Keep explanations technically accurate but "
    "easy to understand.\n\n"

    "These are oral technical interviews. Recommendations should focus on verbal explanation, technical "
    "understanding, interview communication, and spoken reasoning rather than writing code unless the "
    "question itself specifically requires code or syntax knowledge.\n\n"

    "============================================================\n"
    "STRICT JSON SCHEMA\n"
    "============================================================\n"
    "{\n"
    "  \"perQuestionScores\": [\n"
    "    {\n"
    "      \"questionId\": int,\n"
    "      \"knowledgeScores\": {\n"
    "        \"accuracy\": int(0..5),\n"
    "        \"depth\": int(0..5),\n"
    "        \"relevance\": int(0..5),\n"
    "        \"examples\": int(0..5),\n"
    "        \"terminology\": int(0..5)\n"
    "      },\n"
    "      \"speechScores\": {\n"
    "        \"fluency\": int(0..5),\n"
    "        \"structure\": int(0..5),\n"
    "        \"pacing\": int(0..5),\n"
    "        \"grammar\": int(0..5)\n"
    "      }\n"
    "    }\n"
    "  ],\n"
    "  \"perQuestionFeedback\": [\n"
    "    {\n"
    "      \"strengths\": string,\n"
    "      \"areasOfImprovement\": string\n"
    "    }\n"
    "  ],\n"
    "  \"recommendedPractice\": {\n"
    "    \"title\": string,\n"
    "    \"description\": string\n"
    "  },\n"
    "  \"speechFluencyFeedback\": {\n"
    "    \"strengths\": string,\n"
    "    \"areasOfImprovement\": string,\n"
    "    \"ratingEmoji\": string,\n"
    "    \"ratingTitle\": string,\n"
    "    \"ratingDescription\": string\n"
    "  },\n"
    "  \"nextSteps\": [\n"
    "    { \"title\": string }\n"
    "  ],\n"
    "  \"finalTip\": {\n"
    "    \"title\": string,\n"
    "    \"description\": string\n"
    "  }\n"
    "}\n\n"

    "Additional requirements:\n"
    "1. Include one perQuestionScores entry for every question provided in per_question.\n"
    "2. Keep perQuestionFeedback in exactly the same question order as perQuestionScores.\n"
    "3. Do not calculate totals, averages, or percentages; the backend handles those calculations.\n"
    "4. Do not return empty arrays when the corresponding questions or feedback are available.\n"
    "5. nextSteps should contain 2-3 practical immediate actions for the candidate.\n"
    "6. recommendedPractice should describe a concrete interview-oriented practice activity based on "
    "the weaknesses observed.\n"
    "7. finalTip should be concise but genuinely useful.\n"
    "8. ratingEmoji must be EXACTLY one of: 'Excellent', 'Good', 'Average', 'Needs-Improvement', 'Poor'.\n"
    "9. Never fabricate technical strengths or weaknesses.\n"
    "10. Never replace specific technical feedback with generic statements.\n"
    "============================================================"
    )
    sys_prompt = sys_prompt.replace("{track}", interview_track or "Technical Role")
    user_content = {
    "per_question": per_question_inputs,
    "computed_metrics": computed_metrics,
    "total_questions": total_questions,
    "guidelines": [
        "Use the candidate's actual transcription as the primary evidence for knowledge evaluation.",
        "Evaluate the question by comparing the candidate's demonstrated concepts against the concepts required by the question.",
        "Do not treat 'I don't know' or 'I'm not sure' as an unattempted response; treat it as a knowledge gap.",
        "Only treat empty, silent, unintelligible, pass, or skip responses as having no usable answer.",
        "Never invent technical knowledge, strengths, examples, or mistakes.",
        "For each question, identify specific technical concepts the candidate got right, missed, misunderstood, or failed to explain.",
        "Make areasOfImprovement educational: explain what was missing or incorrect, why it matters, and what the candidate should learn or practice.",
        "Do not give generic feedback such as 'improve your technical knowledge', 'add more detail', or 'practice more' without naming the exact concept or skill.",
        "Do not force positive feedback when the candidate did not demonstrate a technical strength.",
        "Keep knowledge feedback separate from speech feedback.",
        "Use computed metrics and speech analysis primarily for speech-related evaluation, while using the candidate response as the primary evidence for knowledge.",
        "Provide 2-3 useful sentences for strengths and areasOfImprovement when sufficient evidence exists.",
        "Ensure speechFluencyFeedback.ratingEmoji is exactly one of: 'Excellent', 'Good', 'Average', 'Needs-Improvement', 'Poor'.",
        "Ensure all feedback is specific to the candidate's actual response and the interview question."
    ],
    }
    # sys_prompt = (
    #     "You are an expert technical interview coach. Given per-question analyses (domain, communication, pace, "
    #     "pause) for interview questions, analyze each question independently and provide scores and feedback.\n\n"
    #     "Your task: \n"
    #     "1. Score each attempted question on knowledge and speech criteria (0-5 scale per criterion)\n"
    #     "2. Provide overall speech fluency feedback across all attempts\n"
    #     "3. Provide per-question simplified feedback for each attempted question\n"
    #     "4. Provide a recommended practice exercise\n"
    #     "5. Provide immediate next steps\n"
    #     "6. Provide a final tip\n\n"
    #     "The code will handle: reportId, candidateInfo, question metadata, totals, averages, and percentages.\n\n"
    #     "Strict JSON schema: {\n"
    #     "  perQuestionScores: [{ questionId: int, knowledgeScores: { accuracy: int(0..5), depth: int(0..5), relevance: int(0..5), examples: int(0..5), terminology: int(0..5) }, speechScores: { fluency: int(0..5), structure: int(0..5), pacing: int(0..5), grammar: int(0..5) } }],\n"
    #     "  perQuestionFeedback: [{ strengths: string, areasOfImprovement: string }],\n"
    #     "  recommendedPractice: { title: string, description: string },\n"
    #     "  speechFluencyFeedback: { strengths: string, areasOfImprovement: string, ratingEmoji: string, ratingTitle: string, ratingDescription: string },\n"
    #     "  nextSteps: [{ title: string }],\n"
    #     "  finalTip: { title: string, description: string }\n"
    #     "}\n\n"
    #     "SCORING GUIDELINES:\n"
    #     "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    #     "PER-QUESTION SCORING (0-5 scale for each criterion):\n"
    #     "\n"
    #     "Knowledge Criteria:\n"
    #     "  - accuracy: How correct and factually accurate was the answer?\n"
    #     "  - depth: How detailed and comprehensive was the explanation?\n"
    #     "  - relevance: How well did the answer address the question?\n"
    #     "  - examples: Quality and appropriateness of examples provided\n"
    #     "  - terminology: Proper use of technical terms and concepts\n"
    #     "\n"
    #     "Speech Criteria:\n"
    #     "  - fluency: Smoothness of speech, minimal hesitations/filler words\n"
    #     "  - structure: Logical organization and clarity of response\n"
    #     "  - pacing: Appropriate speech speed (not too fast/slow)\n"
    #     "  - grammar: Correct sentence structure and language use\n"
    #     "\n"
    #     "CRITICAL - DETECTING NON-ANSWERS:\n"
    #     "⚠️  If the candidate's transcription shows they did NOT provide a real answer, treat it as unattempted:\n"
    #     "   - Responses like 'I don't know', 'I'm not sure', 'pass', 'skip', or very short non-answers (< 10 words)\n"
    #     "   - In these cases: Give ALL knowledge scores as 0, keep speech scores reasonable if they spoke\n"
    #     "   - In feedback strengths: Leave empty or say 'None identified'\n"
    #     "   - In feedback areasOfImprovement: State 'No substantial answer provided' or 'Question not answered'\n"
    #     "   - DO NOT hallucinate feedback about content that wasn't provided\n"
    #     "   - DO NOT give positive feedback if there was no real attempt at answering\n"
    #     "\n"
    #     "IMPORTANT NOTES:\n"
    #     "1. perQuestionScores: Include scores for ALL questions provided in per_question data\n"
    #     "2. perQuestionFeedback: Array corresponding to perQuestionScores order (same length)\n"
    #     "   - Each entry must have SPECIFIC, NON-EMPTY feedback based on the candidate's actual response\n"
    #     "   - strengths: A SINGLE concise sentence summarizing what they did well (or 'None identified' if no answer).\n"
    #     "   - areasOfImprovement: A SINGLE concise sentence summarizing what was missing or weak.\n"
    #     "3. Base scores on the computed_metrics and analysis data provided for each question\n"
    #     "4. Each criterion is scored independently on 0-5 scale\n"
    #     "5. DO NOT calculate totals, averages, or percentages - code will do this\n"
    #     "6. speechFluencyFeedback: Focus ONLY on speech aspects across all attempts. ratingEmoji must be EXACTLY one of: 'Excellent', 'Good', 'Average', 'Needs-Improvement', 'Poor'\n"
    #     "7. DO NOT return empty arrays - every question MUST have meaningful, specific feedback\n"
    #     "8. Keep language simple and actionable - avoid jargon like 'WPM'\n"
    #     "9. These were all oral interviews so your recommendations should not be about things like writing code\n"
    #     "10. nextSteps: Provide 2-3 immediate next steps (titles only)\n"
    #     "11. finalTip: A concluding tip for the candidate\n"
    #     "12. NEVER provide positive feedback if the candidate said 'I don't know' or gave a non-answer"
    #     "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    # )

    # user_content = {
    #     "per_question": per_question_inputs,
    #     "computed_metrics": computed_metrics,
    #     "total_questions": total_questions,
    #     "guidelines": [
    #         "Base all numeric scores on provided computed_metrics and per-question analyses",
    #         "Focus speechFluencyFeedback ONLY on speech aspects, not knowledge",
    #         "Provide specific, actionable feedback grounded in observed patterns",
    #         "Use simple language; avoid jargon like 'WPM' or overly technical terms",
    #         "Connect improvement suggestions to specific weaknesses observed in analyses",
    #         "For per-question feedback, provide ONLY single-sentence summaries for strengths and improvements"
    #     ],
    # }
    logger.info(
        "Calling structured_output for LITE summary synthesis "
        "schema=%s input_count=%s computed_metrics_keys=%s",
        NewStrictSummarySynthesisLLMLite.__name__,
        len(per_question_inputs),
        list(computed_metrics.keys()) if isinstance(computed_metrics, dict) else type(computed_metrics).__name__,
    )
    result, error, latency_ms, model = await structured_output(
        NewStrictSummarySynthesisLLMLite,
        system_prompt=sys_prompt,
        user_content=user_content,
        temperature=0,
    )

    logger.info(
        "structured_output returned for LITE summary synthesis "
        "result_present=%s error_present=%s latency_ms=%s model=%s",
        result is not None,
        bool(error),
        latency_ms,
        model,
    )

    if error:
        logger.error(
            "LITE summary synthesis LLM error: %s",
            error,
        )

    data: dict = {}
    if result:
        logger.info(
            "LITE summary synthesis Pydantic result validated successfully schema=%s",
            type(result).__name__,
        )
        data = result.model_dump()
    else:
        logger.error(
            "LITE summary synthesis returned NO validated result error=%s",
            error,
        )
    return data, error, latency_ms, model

# Base classes for common patterns
class BaseAnalysisLLM(pydantic.BaseModel):
    """Base class for analysis responses with common fields."""
    overall_score: float | None = None
    criteria: dict[str, Any] | None = None
    summary: str | None = None
    strengths: list[str] | None = None
    improvements: list[str] | None = None
    suggestions: list[str] | None = None  # Deprecated, use improvements
    confidence: float | None = None


class BaseItemLLM(pydantic.BaseModel):
    """Base class for item responses with common fields."""
    text: str
    topic: str | None = None
    difficulty: str | None = None


class QuestionsItemLLM(BaseItemLLM):
    """Question item with category field and extra details."""
    category: str | None = None  # tech | tech_allied | behavioral
    keywords: list[str] | None = None
    concepts_covered: list[str] | None = None
    expected_answer: str | None = None
    example_output: str | None = None


class QuestionsResponseLLM(pydantic.BaseModel):
    """Response containing structured question items."""
    items: list[QuestionsItemLLM] = pydantic.Field(default_factory=list)


class DomainAnalysisLLM(BaseAnalysisLLM):
    """Domain knowledge analysis with specific fields."""
    misconceptions: dict[str, Any] | None = None
    examples: dict[str, Any] | None = None


class CommunicationAnalysisLLM(BaseAnalysisLLM):
    """Communication analysis with specific fields."""
    jargon_use: dict[str, Any] | None = None
    tone_empathy: dict[str, Any] | None = None


class PausesSuggestionLLM(pydantic.BaseModel):
    modified_transcript: str= Field(
        ..., 
        validation_alias=AliasChoices("modified_transcript", "transcript")
    )


class PauseCoachLLM(pydantic.BaseModel):
    actionable_feedback: str
    score: int


T = TypeVar('T', bound=pydantic.BaseModel)

async def structured_output(
    model_class: Type[T],
    *,
    system_prompt: str,
    user_content: Any,
    temperature: float = 0,
    max_tokens: int = 2048,
) -> tuple[T | None, str | None, int | None, str]:
    """Call the configured LLM provider (OpenAI or DeepSeek, see LLM_PROVIDER)
    asynchronously with JSON response_format and validate against a Pydantic model."""
    provider = get_active_llm_provider()
    model, api_key = get_active_llm_model_and_key()
    logger.info(
        "[LLM] structured_output START | provider=%s | model=%s | schema=%s | temperature=%s",
        provider,
        model,
        model_class.__name__,
        temperature,
    )
    if not api_key:
        logger.warning(
            "[LLM] structured_output SKIPPED | reason=missing_%s_api_key | model=%s | schema=%s",
            provider,
            model,
            model_class.__name__,
        )
        return None, None, None, model

    start = time.perf_counter()
    try:
        client = get_llm_client()
        if client is None:
            return None, None, None, model
        # gpt-5/o3/o4/gpt-4.1 require max_completion_tokens instead of max_tokens -
        # an OpenAI-specific quirk for their newer/reasoning model families. DeepSeek
        # doesn't have this distinction, so only check it when provider is openai.
        raw = "{}"
        is_new_family = provider == "openai" and any(
            model.lower().startswith(p) for p in ("gpt-5", "gpt-4o", "gpt-4.1", "o4", "o3")
        )
        token_param_key = "max_completion_tokens" if is_new_family else "max_tokens"
        formatted_system_prompt = system_prompt
        if "json" not in formatted_system_prompt.lower():
            formatted_system_prompt += "\n\nIMPORTANT: Respond strictly in valid JSON format."
        logger.info(
            "[LLM] %s REQUEST | model=%s | schema=%s | family=%s | token_param=%s",
            provider,
            model,
            model_class.__name__,
            "new" if is_new_family else "legacy",
            token_param_key,
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "response_format": {"type": "json_object"},
            token_param_key: max_tokens,
            "messages": [
                {"role": "system", "content": formatted_system_prompt},
                {"role": "user", "content": user_content if isinstance(user_content, str) else json.dumps(user_content, ensure_ascii=False)},
            ],
        }
        # Only include temperature for older models; new families accept only the default
        if not is_new_family:
            kwargs["temperature"] = temperature
        request_start = time.perf_counter()
        resp = await client.chat.completions.create(**kwargs)
        openai_latency_ms = int(
            (time.perf_counter() - request_start) * 1000
        )
        logger.info(
            "[LLM] %s RESPONSE | model=%s | schema=%s | latency_ms=%s | choices=%s",
            provider,
            model,
            model_class.__name__,
            openai_latency_ms,
            len(resp.choices) if resp.choices else 0,
        )
        raw = resp.choices[0].message.content or "{}"
        logger.debug(
            "[LLM] RAW RESPONSE | schema=%s | response_length=%s | response_preview=%s",
            model_class.__name__,
            len(raw),
            raw[:1000],
        )
        # -----------------------------
        # JSON parsing
        # -----------------------------
        try:
            data = json.loads(raw)

            logger.debug(
                "[LLM] JSON PARSE SUCCESS | schema=%s | type=%s | keys=%s",
                model_class.__name__,
                type(data).__name__,
                list(data.keys()) if isinstance(data, dict) else None,
            )

        except json.JSONDecodeError as json_error:
            latency_ms = int(
                (time.perf_counter() - start) * 1000
            )

            logger.error(
                "[LLM] JSON PARSE FAILED | model=%s | schema=%s | latency_ms=%s | error=%s | raw_preview=%s",
                model,
                model_class.__name__,
                latency_ms,
                str(json_error),
                raw[:2000],
            )

            return None, str(json_error), latency_ms, model

        # -----------------------------
        # Pydantic validation
        # -----------------------------
        try:
            parsed = model_class.model_validate(data)

            latency_ms = int(
                (time.perf_counter() - start) * 1000
            )

            logger.info(
                "[LLM] PYDANTIC VALIDATION SUCCESS | model=%s | schema=%s | latency_ms=%s",
                model,
                model_class.__name__,
                latency_ms,
            )

            return parsed, None, latency_ms, model

        except pydantic.ValidationError as validation_error:
            latency_ms = int(
                (time.perf_counter() - start) * 1000
            )

            logger.error(
                "[LLM] PYDANTIC VALIDATION FAILED | model=%s | schema=%s | latency_ms=%s | error_count=%s",
                model,
                model_class.__name__,
                latency_ms,
                len(validation_error.errors()),
            )

            logger.error(
                "[LLM] PYDANTIC VALIDATION DETAILS | schema=%s | errors=%s",
                model_class.__name__,
                validation_error.errors(),
            )

            logger.error(
                "[LLM] PYDANTIC INVALID DATA | schema=%s | data=%s",
                model_class.__name__,
                data,
            )

            return None, str(validation_error), latency_ms, model

    except Exception as e:
        latency_ms = int(
            (time.perf_counter() - start) * 1000
        )

        logger.exception(
            "[LLM] STRUCTURED OUTPUT FAILED | provider=%s | model=%s | schema=%s | latency_ms=%s | exception_type=%s | error=%s",
            provider,
            model,
            model_class.__name__,
            latency_ms,
            type(e).__name__,
            str(e),
        )

        return None, str(e), latency_ms, model
        # data = json.loads(raw)
        # parsed = model_class.model_validate(data)
        # latency_ms = int((time.perf_counter() - start) * 1000)
    #     return parsed, None, latency_ms, model
    # except Exception as e:  # noqa: BLE001
    #     latency_ms = int((time.perf_counter() - start) * 1000)
    #     return None, str(e), latency_ms, model


async def extract_resume_entities_with_llm(text: str) -> tuple[list[str], float | None, str | None, int | None, str]:
    model = settings.OPENAI_MODEL
    api_key = settings.OPENAI_API_KEY
    if not api_key or not text:
        return [], None, None, None, model

    start = time.perf_counter()
    error: str | None = None
    skills: list[str] = []
    years: float | None = None

    system_prompt = (
        "Extract skills and years of professional experience from the resume text. "
        "Return ONLY a valid JSON object with exactly these keys: "
        '{"skills": ["skill1", "skill2", ...], "years_experience": number_or_null}. '
        "Do not include any markdown formatting, explanations, or other text. "
        "For skills, extract technical skills, programming languages, tools, and frameworks. "
        "For years_experience, calculate total professional work experience as a number."
    )
    input_text = text[:20000]

    try:
        result, perr, latency, model = await structured_output(
            ResumeEntitiesLLM,
            system_prompt=system_prompt,
            user_content=input_text,
            temperature=0,
        )
        error = perr
        if result:
            skills = result.skills
            years = result.years_experience
        latency_ms = latency
    except Exception as e:
        error = str(e)

    latency_ms = int((time.perf_counter() - start) * 1000)
    return skills, years, error, latency_ms, model


async def extract_resume_entities_v2_with_llm(text: str) -> tuple[dict[str, Any], str | None, int | None, str]:
    """Extended resume extraction including education, experience, projects, and contact info.

    Returns (data_dict, error, latency_ms, model). On missing API key or empty text, returns empty dict and no error.
    """
    model = settings.OPENAI_MODEL
    api_key = settings.OPENAI_API_KEY
    if not api_key or not text:
        return {}, None, None, model

    sys_prompt = (
        "Extract structured resume details from the provided text. "
        "Return ONLY valid JSON matching this schema: {\n"
        "  skills: string[],\n"
        "  years_experience: number|null,\n"
        "  full_name: string|null, email: string|null, phone: string|null, location: string|null,\n"
        "  links: string[]|null, summary: string|null,\n"
        "  education: [{ degree?: string, institution?: string, start_date?: string, end_date?: string }] | null,\n"
        "  experience: [{ company?: string, role?: string, start_date?: string, end_date?: string, responsibilities?: string[], technologies?: string[] }] | null,\n"
        "  projects: [{ name?: string, description?: string, technologies?: string[], link?: string }] | null,\n"
        "  certifications: string[]|null, languages: string[]|null, job_titles: string[]|null, companies: string[]|null\n"
        "}. Dates should be simple strings (e.g., 'Jan 2021' or '2021-01'). Do not include markdown."
    )
    input_text = text[:20000]

    try:
        result, error, latency_ms, model = await structured_output(
            ResumeEntitiesV2LLM,
            system_prompt=sys_prompt,
            user_content=input_text,
            temperature=0,
        )
        data: dict[str, Any] = result.model_dump() if result else {}
        return data, error, latency_ms, model
    except Exception as e:  # noqa: BLE001
        # Fallback to minimal response on error
        latency_ms = None
        return {}, str(e), latency_ms, model


async def extract_jd_skills_with_llm(text: str) -> tuple[list[str], str | None]:
    """
    Extract skills (technical, tools, soft skills, domain-specific) from a Job Description.
    Returns (skills_list, error).
    """
    model = settings.OPENAI_MODEL
    api_key = settings.OPENAI_API_KEY
    if not api_key or not text:
        return [], "OpenAI API key not configured or empty text provided."

    error: str | None = None
    skills: list[str] = []

    sys_prompt = (
        "Extract all relevant professional skills from the provided Job Description text. "
        "Include technical skills, tools, frameworks, soft skills, and domain-specific methodologies "
        "(e.g., 'User Research', 'Prototyping', 'Figma', 'Agile'). "
        "Return ONLY a valid JSON object matching the schema: "
        '{"skills": ["skill1", "skill2", ...]}'
    )
    input_text = text[:15000] # Limit size to prevent token limits

    try:
        result, perr, latency, model = await structured_output(
            JobDescriptionSkillsLLM,
            system_prompt=sys_prompt,
            user_content=input_text,
            temperature=0,
        )
        error = perr
        if result:
            skills = result.skills
    except Exception as e:
        error = f"Failed to extract skills: {str(e)}"

    return skills, error


async def generate_interview_questions_with_llm(
    track: str,
    context_text: str | None = None,
    count: int = 3,
    difficulty: str | None = None,
    *,
    syllabus_topics: dict[str, list[str]] | None = None,
    ratio: dict[str, int] | None = None,
    influence: dict[str, Any] | None = None,
) -> tuple[list[str], str | None, int | None, str, list[dict[str, Any]] | None]:
    """
    Generate interview questions using an LLM given a track and optional context (e.g., resume_text).
    Returns (questions, error, latency_ms, model). On missing API key, returns empty questions and no error.
    """
    model = settings.OPENAI_MODEL
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        return [], None, None, model, None

    start = time.perf_counter()
    error: str | None = None
    questions: list[str] = []
    structured_items: list[dict[str, Any]] | None = None
    total = max(1, min(50, count or 3))

    sys_prompt = (
        "You are an expert technical interviewer generating a set of exactly {count} interview questions for a candidate in the {track} role.\n\n"
        "STRICT CATEGORY DISTRIBUTION MANDATE:\n"
        "You MUST generate the questions following this EXACT category mix:\n"
        "- Tech (core domain technical questions): 2 questions\n"
        "- Tech Allied (related tools, architecture, databases, or workflow): 2 questions\n"
        "- Behavioral (soft skills, past conflict, team collaboration, STAR method): 1 question\n\n"
        "RULES FOR QUESTION GENERATION:\n"
        "1. The 'category' field for each item MUST strictly be set to one of: 'tech', 'tech_allied', or 'behavioral'.\n"
        "2. Ensure questions are suitable for spoken verbal answers (no coding or writing code).\n"
        "3. Ask deep, targeted technical and situational questions that require thoughtful answers.\n"
        "4. Return ONLY valid JSON with key 'items' containing array of objects with fields: text, topic, difficulty, category, keywords, concepts_covered, expected_answer, example_output."
    ).format(count=total, track=track)
        # "You are an expert interviewer. Generate concise, specific interview questions for a candidate. "
        # "Avoid open-ended prompts; ask targeted questions that require concrete answers, but keep in mind to ask deep questions that will take time to answer NOT one sentence or one word answers"
        # "Return ONLY valid JSON with key: 'items' (array of objects with fields: text, topic, difficulty, category, keywords, concepts_covered, expected_answer, example_output)."
        # "Understand that this is a verbal interview setting, so questions should STRICTLY be suitable for strictly spoken responses."
    knowledge_reference_context = (influence or {}).get("knowledge_reference_context")
    if knowledge_reference_context:
        sys_prompt += (
            f"\n\nReference Knowledge Base:\n{knowledge_reference_context}\n\n"
            "Instructions:\n"
            "Use the above uploaded knowledge base only as topic guidance.\n"
            "Generate interview questions related to these reference questions.\n"
            "Do not copy reference questions exactly.\n"
            "Generate new related questions using the same topics/concepts.\n"
            "Keep the questions aligned with the job profile, skills, experience level, and requested difficulty level.\n"
            "Return the same structured output as before:\n"
            "question, keywords, concepts_covered, expected_answer, example_output, level, difficulty, type."
        )
    # Prepare a sampled syllabus so we don't send the entire topic bank to the LLM
    topics = syllabus_topics or {}
    r = ratio or {"tech": 2, "tech_allied": 2, "behavioral": 1}
    # Normalize ratio to total questions (we use it only as guidance for sampling size)
    r_tech = max(0, r.get("tech", 0))
    r_allied = max(0, r.get("tech_allied", 0))
    r_beh = max(0, r.get("behavioral", 0))

    def _pick(ls: list[str] | None, n: int) -> list[str]:
        pool = list(ls or [])
        if not pool:
            return []
        k = min(len(pool), max(1, n))
        # random.sample requires k <= len(pool)
        return random.sample(pool, k)

    # Heuristic: provide up to 2x topics per expected question in that category (min 3)
    tech_pool = _pick(topics.get("tech"), max(2, r_tech * 2))
    allied_pool = _pick(topics.get("tech_allied"), max(2, r_allied * 2))
    beh_pool_full = list(topics.get("behavioral", []))
    beh_pool = _pick(beh_pool_full, max(3, r_beh * 2 if r_beh > 0 else 3))

    sampled_syllabus = {
        "tech": tech_pool,
        "tech_allied": allied_pool,
        # Keep behavioral also as part of syllabus for uniformity; LLM will still honor categories
        "behavioral": beh_pool,
    }

    exclude_list = (influence or {}).get("exclude_questions", [])
    constraints = [
        "No preambles, no numbering in the JSON itself",
        "Questions should be single sentences when possible",
        "Avoid duplicate or trivial questions",
        "Each item must include a 'category' of tech | tech_allied | behavioral",
        "Behavioral questions must come from the provided behavioral topics and probe for specific actions/decisions",
        "Tech-allied questions should be related to the candidate's experience/skills when available",
        "Vary topics and ensure depth appropriate to difficulty; do not ask purely opinion-based questions",
        "Use a mix of the provided archetypes to ensure variety (e.g., concept, trade-offs, debug, design)",
        "Follow the depth guidelines for the given difficulty",
        "Ask deep questions but make sure they have a clear, specific answer"
    ]
    if exclude_list:
        constraints.append(f"Do NOT generate any questions similar or identical to these existing questions: {exclude_list}")

    user_prompt = {
        "track": track,
        "count": total,
        "context": (context_text or "")[:4000],
        "difficulty": (difficulty or "medium"),
        # Category mix and topics per product requirements
        "categories": {
            "definitions": {
                "tech": "Core technical questions for the target role",
                "tech_allied": "Technical questions allied to the candidate's background/experience",
                "behavioral": "Behavioral questions from the provided list",
            },
            "ratio": ratio or {"tech": 2, "tech_allied": 2, "behavioral": 1},
        },
        # Only pass a random subset of topics so the model focuses and varies questions over runs
        "syllabus": sampled_syllabus,
        "archetypes": (syllabus_topics or {}).get("archetypes", []),
        "depth_guidelines": (syllabus_topics or {}).get("depth_guidelines", []),
        # Also trim behavioral list passed separately to reinforce selection
        "behavioral_topics": beh_pool,
        "influence": influence or {},
        "constraints": constraints,
    }

    try:
        result, perr, latency, model = await structured_output(
            QuestionsResponseLLM,
            system_prompt=sys_prompt,
            user_content=user_prompt,
            temperature=0.2,
        )
        error = perr
        if result:
            # Extract questions from items and create structured items
            if result.items:
                questions = [it.text.strip() for it in result.items]
                structured_items = [
                    {
                        "text": it.text.strip(),
                        "topic": it.topic,
                        "difficulty": it.difficulty,
                        "category": it.category,
                        "keywords": it.keywords or [],
                        "concepts_covered": it.concepts_covered or [],
                        "expected_answer": it.expected_answer,
                        "example_output": it.example_output,
                    }
                    for it in result.items
                ]
        latency_ms = latency
    except Exception as e:
        error = str(e)

    latency_ms = int((time.perf_counter() - start) * 1000)
    return questions, error, latency_ms, model, structured_items


async def generate_follow_up_question(
    *,
    track: str,
    difficulty: str,
    base_question: str,
    answer_excerpt: str,
    topic: str | None = None,
) -> tuple[str | None, str | None, int | None, str]:
    """
    Generate a concise follow-up question using the candidate's recent answer excerpt.
    """
    model = settings.OPENAI_MODEL
    api_key = settings.OPENAI_API_KEY
    if not api_key or not answer_excerpt:
        return None, "Follow-up generation skipped (missing API key or answer excerpt)", None, model

    system_prompt = (
        "You are an attentive interviewer. Craft ONE short follow-up question based on the candidate's prior answer. "
        "Keep it conversational, focus on clarifying depth, and avoid yes/no prompts. "
        "Return ONLY valid JSON with key 'question'."
    )
    payload = {
        "track": track,
        "difficulty": difficulty,
        "topic": topic,
        "base_question": base_question,
        "answer_excerpt": answer_excerpt[:4000],
        "rules": [
            "Follow-up must relate directly to the candidate's answer.",
            "Avoid repeating the original question text.",
            "Keep it under 35 words.",
            "Encourage the candidate to elaborate or clarify specifics.",
        ],
    }
    result, error, latency_ms, model = await structured_output(
        FollowUpQuestionLLM,
        system_prompt=system_prompt,
        user_content=payload,
        temperature=0.35,
    )
    question = result.question.strip() if result else None
    return question, error, latency_ms, model


async def generate_question_supplements_with_llm(
    question_payload: list[dict[str, Any]],
) -> tuple[list[LLMSupplementItem], str | None, int | None, str]:
    """
    Generate supplemental snippets (diagram or code) for interview questions.
    Returns list of LLMSupplementItem entries and metadata about the call.
    """
    model = settings.OPENAI_MODEL
    api_key = settings.OPENAI_API_KEY
    if not api_key or not question_payload:
        return [], None, None, model

    system_prompt = (
        "You are an AI assistant that supplies concise, high-signal supplements for EVERY interview question. "
        "For each question, emit exactly one supplement: either a readable code snippet (<=20 lines) "
        "or a simple Mermaid diagram (<=20 lines). Prefer code for procedural/algorithmic topics and "
        "Mermaid for flows/architecture. Always return valid JSON with an 'items' array."
    )

    user_content = {
        "instructions": [
            "Use supplementType 'code' for source snippets, 'diagram' for Mermaid diagrams.",
            "Include a 'format' value such as a programming language (python, javascript, sql) or 'mermaid' for diagrams.",
            "Do not exceed 20 lines in the content; focus on runnable pseudocode or clearly labelled steps.",
            "Return exactly one supplement per question; if stuck, provide a minimal scaffold that still helps the candidate orient.",
            "OUTPUT JSON shape: {\"items\": [{\"questionId\": <int>, \"supplementType\": \"code\"|\"diagram\", \"format\": \"javascript\"|\"python\"|\"sql\"|\"mermaid\", \"content\": \"string\"}]}",
            "Use the questionId from the provided payload verbatim.",
        ],
        "questions": question_payload,
        "example": {
            "items": [
                {
                    "questionId": 123,
                    "supplementType": "code",
                    "format": "javascript",
                    "content": "function debounce(fn, wait = 200) {\n  let t;\n  return (...args) => {\n    clearTimeout(t);\n    t = setTimeout(() => fn(...args), wait);\n  };\n}",
                },
                {
                    "questionId": 456,
                    "supplementType": "diagram",
                    "format": "mermaid",
                    "content": "flowchart LR\n  UI-->API\n  API-->DB\n  DB-->Cache\n  Cache-->UI",
                }
            ]
        },
    }

    result, error, latency_ms, model = await structured_output(
        LLMSupplementResponse,
        system_prompt=system_prompt,
        user_content=user_content,
        temperature=0.3,
    )

    def _trim_content(content: str, max_lines: int = 20) -> str:
        lines = (content or "").splitlines()
        if not lines:
            return ""
        trimmed = lines[:max_lines]
        # Remove trailing blank lines
        while trimmed and not trimmed[-1].strip():
            trimmed.pop()
        return "\n".join(trimmed).strip()

    def _sanitize_mermaid(content: str) -> str:
        c = content.strip()
        was_wrapped = False
        if c.startswith("```mermaid"):
            c = c[len("```mermaid"):].strip()
            was_wrapped = True
        if c.endswith("```"):
            c = c[:-3].strip()
        if was_wrapped:
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"[Mermaid Sanitizer] Before:\n{content}\nAfter:\n{c}")
        return c

    sanitized: list[LLMSupplementItem] = []
    if result:
        for item in result.items:
            snippet = _trim_content(item.content)
            if not snippet:
                continue
            supplement_type = item.supplementType.lower()
            if supplement_type not in {"code", "diagram"}:
                continue
            fmt = (item.format or "").strip() or ("mermaid" if supplement_type == "diagram" else None)
            
            if supplement_type == "diagram" and fmt == "mermaid":
                snippet = _sanitize_mermaid(snippet)
                
            sanitized.append(
                LLMSupplementItem(
                    questionId=item.questionId,
                    supplementType=supplement_type,
                    format=fmt,
                    content=snippet,
                )
            )

    return sanitized, error, latency_ms, model


MIN_ANSWER_WORD_COUNT = 3  # below this, treat as no real answer rather than scoring it


def _is_near_empty_answer(transcription: str | None) -> bool:
    """True if the transcription has too few words to be a real attempt."""
    word_count = len((transcription or "").strip().split())
    return word_count < MIN_ANSWER_WORD_COUNT


def _build_non_answer_analysis(kind: str) -> dict[str, Any]:
    """
    Deterministic, zero-cost result for a near-empty answer — skips the LLM
    entirely so scoring never depends on the model correctly guessing that
    an (almost) blank transcription deserves a zero, rather than a
    plausible-looking partial-credit score.
    """
    base = {
        "overall_score": 0,
        "criteria": {},
        "summary": "No substantial answer was provided for this question.",
        "strengths": [],
        "improvements": ["No substantial answer provided — question not answered."],
        "suggestions": [],
        "confidence": 1.0,
    }
    if kind == "domain":
        base["misconceptions"] = {"present": False, "notes": []}
        base["examples"] = {"present": False, "notes": []}
    else:
        base["jargon_use"] = {"score": 0, "notes": []}
        base["tone_empathy"] = {"score": 0, "notes": []}
    return base


async def analyze_domain_with_llm(
    *,
    user_profile: dict[str, Any],
    question_text: str | None,
    transcription: str,
) -> tuple[dict[str, Any], str | None, int | None, str]:
    """
    Perform domain knowledge analysis using LLM. Returns (analysis_json, error, latency_ms, model).
    Never raises; on missing API key returns empty analysis and no error.
    """
    model = settings.OPENAI_MODEL
    api_key = settings.OPENAI_API_KEY
    logger.info(
        "[LLM DOMAIN] START | model=%s | question_length=%s | transcription_length=%s",
        model,
        len(question_text or ""),
        len(transcription or ""),
    )
    if _is_near_empty_answer(transcription):
        logger.info("[LLM DOMAIN] SHORT-CIRCUIT | reason=near_empty_answer")
        return _build_non_answer_analysis("domain"), None, 0, model
    if not api_key:
        logger.warning(
            "[LLM DOMAIN] SKIPPED | reason=missing_openai_api_key"
        )
        return {}, None, None, model

    start = time.perf_counter()
    error: str | None = None
    analysis: dict[str, Any] = {}

    sys_prompt = (
        "You are a strict technical interviewer. Assess the candidate's domain knowledge based on the transcript. "
        "Return ONLY valid JSON with keys: overall_score (0-100), criteria (object with correctness/depth/coverage/"
        "relevance each having score (0-100) and reasons (string[]), misconceptions (present: bool, notes: string[]), "
        "examples (present: bool, notes: string[])), summary (string), strengths (string[] of positive aspects), "
        "improvements (string[] of areas to improve), confidence (0-1). "
        "IMPORTANT: Always include both strengths and improvements arrays, even if scores are low. "
        "Strengths should highlight what the candidate did well, even if partial. "
        "Improvements should provide actionable feedback for growth. "
        "CRITICAL: If the transcript is a refusal or non-answer (e.g. 'I don't know', 'I'm not sure', 'pass'), or "
        "doesn't actually attempt to address the question at all, give overall_score 0 and every criteria score 0, "
        "leave strengths empty, and set improvements to state clearly that no substantial answer was given — do not "
        "invent partial credit or plausible-sounding feedback for content that wasn't actually said."
    )
    user_content = {
        "user_profile": {k: v for k, v in user_profile.items() if v is not None},
        "question": question_text or "",
        "transcription": (transcription or "")[:8000],
    }

    def _clean_and_parse_json(raw_text: str) -> dict[str, Any]:
        text = (raw_text or "").strip()
        # Strip common code fences
        if text.startswith("```json"):
            text = text[7:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            # Best-effort extraction of first JSON object/array
            start_obj = text.find('{')
            start_arr = text.find('[')
            start = min(x for x in [start_obj, start_arr] if x != -1) if (start_obj != -1 or start_arr != -1) else -1
            end_obj = text.rfind('}')
            end_arr = text.rfind(']')
            end = max(end_obj, end_arr)
            if start != -1 and end != -1 and end > start:
                candidate = text[start:end+1]
                # Remove trailing commas before closing braces/brackets
                import re as _re
                candidate = _re.sub(r',\s*(\}|\])', r'\1', candidate)
                return json.loads(candidate)
            raise ValueError("LLM response did not contain valid JSON")

    result, error, latency_ms, model = await structured_output(
        DomainAnalysisLLM,
        system_prompt=sys_prompt,
        user_content=user_content,
        temperature=0,
    )
    if error:
        logger.error(
            "[LLM DOMAIN] FAILED | model=%s | latency_ms=%s | error=%s",
            model,
            latency_ms,
            error,
        )
    elif result:
        logger.info(
            "[LLM DOMAIN] SUCCESS | model=%s | latency_ms=%s",
            model,
            latency_ms,
        )
    else:
        logger.warning(
            "[LLM DOMAIN] EMPTY RESULT | model=%s | latency_ms=%s",
            model,
            latency_ms,
        )

    analysis: dict[str, Any] = {}
    if result:
        analysis = result.model_dump()
    return analysis, error, latency_ms, model


async def analyze_communication_with_llm(
    *,
    user_profile: dict[str, Any],
    question_text: str | None,
    transcription: str,
    aux_metrics: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None, int | None, str]:
    """
    Perform communication analysis using LLM. Returns (analysis_json, error, latency_ms, model).
    Never raises; on missing API key returns empty analysis and no error.
    """
    model = settings.OPENAI_MODEL
    api_key = settings.OPENAI_API_KEY
    logger.info(
        "[LLM COMM] START | model=%s | question_length=%s | transcription_length=%s | aux_metrics_keys=%s",
        model,
        len(question_text or ""),
        len(transcription or ""),
        len(aux_metrics or ""),
    )
    if _is_near_empty_answer(transcription):
        logger.info("[LLM COMM] SHORT-CIRCUIT | reason=near_empty_answer")
        return _build_non_answer_analysis("communication"), None, 0, model
    if not api_key:
        logger.warning(
            "[LLM COMM] SKIPPED | reason=missing_openai_api_key"
        )
        return {}, None, None, model

    start = time.perf_counter()
    error: str | None = None
    analysis: dict[str, Any] = {}

    sys_prompt = (
        "You are a communication coach. Assess clarity, structure, coherence, conciseness, jargon use, and tone/empathy. "
        "Return ONLY valid JSON with keys: overall_score (0-100), criteria (object with clarity/structure/coherence/"
        "conciseness each having score (0-100) and reasons (string[]), jargon_use (score:number, notes:string[]), "
        "tone_empathy (score:number, notes:string[])), summary (string), strengths (string[] of positive aspects), "
        "improvements (string[] of areas to improve), suggestions (string[] for backward compatibility), confidence (0-1). "
        "Always include both strengths and improvements arrays, even if scores are low. "
        "Strengths should highlight what the candidate did well. Improvements should identify specific areas to work on. "
        "Heavily penalize short answers that dont have enough nuance and detail. "
        "CRITICAL: If the transcript is a refusal or non-answer (e.g. 'I don't know', 'I'm not sure', 'pass'), or "
        "doesn't actually attempt to address the question at all, give overall_score 0 and every criteria score 0, "
        "leave strengths empty, and set improvements to state clearly that no substantial answer was given — do not "
        "invent partial credit or plausible-sounding feedback for content that wasn't actually said."
    )
    payload = {
        "user_profile": {k: v for k, v in user_profile.items() if v is not None},
        "question": question_text or "",
        "transcription": (transcription or "")[:8000],
        "aux_metrics": aux_metrics or {},
    }

    def _clean_and_parse_json(raw_text: str) -> dict[str, Any]:
        text = (raw_text or "").strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            start_obj = text.find('{')
            start_arr = text.find('[')
            start = min(x for x in [start_obj, start_arr] if x != -1) if (start_obj != -1 or start_arr != -1) else -1
            end_obj = text.rfind('}')
            end_arr = text.rfind(']')
            end = max(end_obj, end_arr)
            if start != -1 and end != -1 and end > start:
                candidate = text[start:end+1]
                import re as _re
                candidate = _re.sub(r',\s*(\}|\])', r'\1', candidate)
                return json.loads(candidate)
            raise ValueError("LLM response did not contain valid JSON")

    result, error, latency_ms, model = await structured_output(
        CommunicationAnalysisLLM,
        system_prompt=sys_prompt,
        user_content=payload,
        temperature=0,
    )

    if error:
        logger.error(
            "[LLM COMM] FAILED | model=%s | latency_ms=%s | error=%s",
            model,
            latency_ms,
            error,
        )
    elif result:
        logger.info(
            "[LLM COMM] SUCCESS | model=%s | latency_ms=%s",
            model,
            latency_ms,
        )
    else:
        logger.warning(
            "[LLM COMM] EMPTY RESULT | model=%s | latency_ms=%s",
            model,
            latency_ms,
        )

    analysis: dict[str, Any] = {}
    if result:
        analysis = result.model_dump()
    return analysis, error, latency_ms, model

