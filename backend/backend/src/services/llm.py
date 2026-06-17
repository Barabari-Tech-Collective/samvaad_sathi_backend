import json
import random
import time
from typing import Any, Type, List, Dict, Literal

import pydantic
from openai import AsyncOpenAI
from src.config.manager import settings
from src.models.schemas.summary_report import SummarySection, SummarySectionGroup, SummaryMetrics

# Lazy client holder; create only when needed and when API key is present
_client: AsyncOpenAI | None = None

def _get_client() -> AsyncOpenAI | None:
    global _client
    if _client is not None:
        return _client
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        return None
    _client = AsyncOpenAI(
        api_key=api_key,
        timeout=float(getattr(settings, "OPENAI_TIMEOUT_SECONDS", 60.0)),
        max_retries=3,
    )
    return _client


class ResumeEntitiesLLM(pydantic.BaseModel):
    skills: list[str] = pydantic.Field(default_factory=list)
    years_experience: float | None = None


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


class NewStrictSummarySynthesisLLMLite(pydantic.BaseModel):
    """Restructured summary report output (Lite) - LLM provides only scores and simplified feedback."""
    perQuestionScores: list[LLMQuestionScores]
    overallFeedback: LLMOverallFeedback
    perQuestionFeedback: list[LLMQuestionFeedbackLite]



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
        "IMPORTANT NOTES:\n"
        "1. perQuestionScores: Include scores for ALL questions provided in per_question data\n"
        "2. perQuestionFeedback: Array corresponding to perQuestionScores order (same length)\n"
        "   - Each entry must have SPECIFIC, NON-EMPTY feedback based on the candidate's actual response\n"
        "   - Include 2-3 specific strengths (what they did well) - ONLY if they actually attempted the question\n"
        "   - Include 2-3 specific areas of improvement (what was missing or weak)\n"
        "   - Include 3-4 actionable insights with clear titles and detailed descriptions\n"
        "3. Base scores on the computed_metrics and analysis data provided for each question\n"
        "4. Each criterion is scored independently on 0-5 scale\n"
        "5. DO NOT calculate totals, averages, or percentages - code will do this\n"
        "6. overallFeedback.speechFluency: Focus ONLY on speech aspects across all attempts (3-4 actionable steps)\n"
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

    result, error, latency_ms, model = await structured_output(
        NewStrictSummarySynthesisLLM,
        system_prompt=sys_prompt,
        user_content=user_content,
        temperature=0,
    )

    data: dict = {}
    if result:
        data = result.model_dump()
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
    perQuestionScores: list[LLMPerQuestionScoresStrict]
    perQuestionFeedback: list[LLMQuestionFeedbackLiteStrict]
    recommendedPractice: LLMRecommendedPracticeStrict
    speechFluencyFeedback: LLMSpeechFluencyFeedbackStrict
    nextSteps: list[LLMNextStepStrict]
    finalTip: LLMFinalTipStrict


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
        "IMPORTANT NOTES:\n"
        "1. perQuestionScores: Include scores for ALL questions provided in per_question data\n"
        "2. perQuestionFeedback: Array corresponding to perQuestionScores order (same length)\n"
        "   - Each entry must have SPECIFIC, NON-EMPTY feedback based on the candidate's actual response\n"
        "   - strengths: A SINGLE concise sentence summarizing what they did well (or 'None identified' if no answer).\n"
        "   - areasOfImprovement: A SINGLE concise sentence summarizing what was missing or weak.\n"
        "3. Base scores on the computed_metrics and analysis data provided for each question\n"
        "4. Each criterion is scored independently on 0-5 scale\n"
        "5. DO NOT calculate totals, averages, or percentages - code will do this\n"
        "6. speechFluencyFeedback: Focus ONLY on speech aspects across all attempts. ratingEmoji must be EXACTLY one of: 'Excellent', 'Good', 'Average', 'Needs-Improvement', 'Poor'\n"
        "7. DO NOT return empty arrays - every question MUST have meaningful, specific feedback\n"
        "8. Keep language simple and actionable - avoid jargon like 'WPM'\n"
        "9. These were all oral interviews so your recommendations should not be about things like writing code\n"
        "10. nextSteps: Provide 2-3 immediate next steps (titles only)\n"
        "11. finalTip: A concluding tip for the candidate\n"
        "12. NEVER provide positive feedback if the candidate said 'I don't know' or gave a non-answer"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    user_content = {
        "per_question": per_question_inputs,
        "computed_metrics": computed_metrics,
        "total_questions": total_questions,
        "guidelines": [
            "Base all numeric scores on provided computed_metrics and per-question analyses",
            "Focus speechFluencyFeedback ONLY on speech aspects, not knowledge",
            "Provide specific, actionable feedback grounded in observed patterns",
            "Use simple language; avoid jargon like 'WPM' or overly technical terms",
            "Connect improvement suggestions to specific weaknesses observed in analyses",
            "For per-question feedback, provide ONLY single-sentence summaries for strengths and improvements"
        ],
    }

    result, error, latency_ms, model = await structured_output(
        NewStrictSummarySynthesisLLMLite,
        system_prompt=sys_prompt,
        user_content=user_content,
        temperature=0,
    )

    data: dict = {}
    if result:
        data = result.model_dump()
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
    """Question item with category field."""
    category: str | None = None  # tech | tech_allied | behavioral


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
    modified_transcript: str


class PauseCoachLLM(pydantic.BaseModel):
    actionable_feedback: str
    score: int


async def structured_output(
    model_class: Type[pydantic.BaseModel],
    *,
    system_prompt: str,
    user_content: Any,
    temperature: float = 0,
) -> tuple[pydantic.BaseModel | None, str | None, int | None, str]:
    """Call OpenAI asynchronously with JSON response_format and validate against Pydantic model."""
    model = settings.OPENAI_MODEL
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        return None, None, None, model

    start = time.perf_counter()
    try:
        client = _get_client()
        if client is None:
            return None, None, None, model
        # Use Chat Completions for all models; switch token param for newer families
        raw = "{}"
        is_new_family = any(str(model).lower().startswith(p) for p in ("gpt-5", "gpt-4.1", "o4", "o3"))
        token_param_key = "max_completion_tokens" if is_new_family else "max_tokens"
        kwargs = {
            "model": model,
            "response_format": {"type": "json_object"},
            token_param_key: 2048,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content if isinstance(user_content, str) else json.dumps(user_content, ensure_ascii=False)},
            ],
        }
        # Only include temperature for older models; new families accept only the default
        if not is_new_family:
            kwargs["temperature"] = temperature
        resp = await client.chat.completions.create(**kwargs)
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        parsed = model_class.model_validate(data)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return parsed, None, latency_ms, model
    except Exception as e:  # noqa: BLE001
        latency_ms = int((time.perf_counter() - start) * 1000)
        return None, str(e), latency_ms, model


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
            skills = [str(x) for x in result.skills]
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

    sys_prompt = (
        "You are an expert interviewer. Generate concise, specific interview questions for a candidate. "
        "Avoid open-ended prompts; ask targeted questions that require concrete answers, but keep in mind to ask deep questions that will take time to answer NOT one sentence or one word answers"
        "Return ONLY valid JSON with key: 'items' (array of objects with fields: text, topic, difficulty, category)."
        "Understand that this is a verbal interview setting, so questions should STRICTLY be suitable for strictly spoken responses."
    )
    # Prepare a sampled syllabus so we don't send the entire topic bank to the LLM
    topics = syllabus_topics or {}
    r = ratio or {"tech": 2, "tech_allied": 2, "behavioral": 1}
    total = max(1, min(10, int(count or 3)))
    # Normalize ratio to total questions (we use it only as guidance for sampling size)
    r_tech = max(0, int(r.get("tech", 0)))
    r_allied = max(0, int(r.get("tech_allied", 0)))
    r_beh = max(0, int(r.get("behavioral", 0)))

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
        "constraints": [
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
        ],
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
                    {"text": it.text.strip(), "topic": it.topic, "difficulty": it.difficulty, "category": it.category}
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
    if not api_key:
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
        "Improvements should provide actionable feedback for growth."
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
    if not api_key:
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
        "Heavily penalize short answers that dont have enough nuance and detail"
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

    analysis: dict[str, Any] = {}
    if result:
        analysis = result.model_dump()
    return analysis, error, latency_ms, model
