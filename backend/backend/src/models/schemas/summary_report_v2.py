"""Schemas for the new V2 summary report (lite version).

This response is restructured to provide a cleaner, more actionable format:
- reportId: Unique identifier for the report
- candidateInfo: Basic interview metadata
- scoreSummary: Numeric scores with max values and percentages
- questionAnalysis: Per-question breakdown with simplified feedback (single line strengths/improvements)
- recommendedPractice: A specific practice exercise recommendation
- speechFluencyFeedback: Detailed speech feedback with rating
- nextSteps: List of immediate next steps
- finalTip: A concluding tip for the candidate
"""

from __future__ import annotations

import pydantic
from typing import List, Union, Any

from src.models.schemas.base import BaseSchemaModel
from src.models.schemas.summary_report import (
    ScoreSummary,
)

class CandidateInfoLite(BaseSchemaModel):
    """Basic candidate and interview information for Lite report."""
    name: str | None = pydantic.Field(default=None, description="Candidate name if available")
    interviewDate: str = pydantic.Field(description="ISO date string for interview")
    roleTopic: str = pydantic.Field(description="Interview track/role (e.g., 'Frontend Development')")
    duration: str | None = pydantic.Field(default=None, description="Duration of the interview (e.g., '25 mins')")
    durationFeedback: str | None = pydantic.Field(default=None, description="Feedback on time management")

class QuestionFeedbackLite(BaseSchemaModel):
    """Simplified feedback for a question with single-line strings."""
    strengths: str | None = pydantic.Field(default=None, description="Single line summary of strengths")
    areasOfImprovement: str | None = pydantic.Field(default=None, description="Single line summary of areas for improvement")

class QuestionAnalysisItemLite(BaseSchemaModel):
    """Individual question analysis with simplified feedback."""
    id: int = pydantic.Field(description="Question ID")
    totalQuestions: int = pydantic.Field(description="Total number of questions in interview")
    type: str = pydantic.Field(description="Question type (e.g., 'Technical question')")
    question: str = pydantic.Field(description="Question text")
    transcript: str | None = pydantic.Field(
        default=None,
        description="The candidate's answer transcript that the feedback below was generated from (null if not attempted).",
    )
    feedback: QuestionFeedbackLite | None = pydantic.Field(default=None, description="Feedback (null if not attempted)")

class RecommendedPracticeLite(BaseSchemaModel):
    """Recommended practice exercise."""
    title: str = pydantic.Field(description="Title of the practice exercise")
    description: str = pydantic.Field(description="Description of the practice exercise")

class SpeechFluencyFeedbackLite(BaseSchemaModel):
    """Detailed speech fluency feedback."""
    strengths: str = pydantic.Field(description="Paragraph describing speech strengths")
    areasOfImprovement: str = pydantic.Field(description="Paragraph describing speech areas for improvement")
    ratingEmoji: str = pydantic.Field(description="Emoji representing the rating (e.g., 'Slightly-happy')")
    ratingTitle: str = pydantic.Field(description="Title of the rating (e.g., 'You are a Clear but Structurally Inconsistent Speaker')")
    ratingDescription: str = pydantic.Field(description="Description of the rating")

class NextStepLite(BaseSchemaModel):
    """Immediate next step for the candidate."""
    title: str = pydantic.Field(description="Title of the next step")

class FinalTipLite(BaseSchemaModel):
    """Concluding tip for the candidate."""
    title: str = pydantic.Field(description="Title of the tip")
    description: str = pydantic.Field(description="Description of the tip")

class SummaryReportResponseLite(BaseSchemaModel):
    """Restructured summary report response matching new V2 format."""
    reportId: str = pydantic.Field(description="Unique report identifier (UUID)")
    candidateInfo: CandidateInfoLite
    scoreSummary: ScoreSummary
    questionAnalysis: List[QuestionAnalysisItemLite]
    recommendedPractice: RecommendedPracticeLite | None = pydantic.Field(default=None, description="Recommended practice exercise")
    speechFluencyFeedback: SpeechFluencyFeedbackLite | None = pydantic.Field(default=None, description="Speech fluency feedback")
    nextSteps: List[Union[NextStepLite, str]] | None = pydantic.Field(default=None, description="List of next steps")
    finalTip: FinalTipLite | None = pydantic.Field(default=None, description="Final tip for candidate")
@pydantic.field_validator("nextSteps", mode="after")
@classmethod
def normalize_next_steps(cls, v: List[Any] | None) -> List[NextStepLite] | None:
    """Automatically convert raw strings like ['Review Node.js'] into [{'title': 'Review Node.js'}]"""
    if v is None:
        return None
    normalized = []
    for item in v:
        if isinstance(item, str):
            normalized.append(NextStepLite(title=item))
        elif isinstance(item, dict):
            normalized.append(NextStepLite(**item))
        elif isinstance(item, NextStepLite):
            normalized.append(item)
        return normalized