"""Evidence-safe schemas for the student Practice report."""

from __future__ import annotations

from typing import Any, Literal

import pydantic

from src.models.schemas.base import BaseSchemaModel
from src.models.schemas.summary_report import ScoreSummary

ReportStatus = Literal["ready", "partial", "needs_review", "insufficient_evidence"]
EvidenceConfidence = Literal["high", "medium", "low", "insufficient"]
QuestionEvidenceStatus = Literal[
    "evaluated", "needs_review", "insufficient", "unavailable", "not_attempted"
]
PracticeModule = Literal["structure", "pronunciation", "pace", "warm_up", "think_fast"]


class CandidateInfoLite(BaseSchemaModel):
    name: str | None = None
    interviewDate: str
    roleTopic: str
    duration: str | None = None
    durationFeedback: str | None = None


class QuestionFeedbackLite(BaseSchemaModel):
    strengths: str | None = None
    areasOfImprovement: str | None = None


class QuestionEvidenceLite(BaseSchemaModel):
    status: QuestionEvidenceStatus
    observation: str | None = None
    interpretation: str | None = None
    uncertainty: str | None = None


class QuestionAnalysisItemLite(BaseSchemaModel):
    id: int
    totalQuestions: int
    type: str
    question: str
    transcript: str | None = None
    feedback: QuestionFeedbackLite | None = None
    evidence: QuestionEvidenceLite | None = None


class RecommendedPracticeLite(BaseSchemaModel):
    module: PracticeModule | None = None
    title: str
    description: str
    href: str | None = None
    reason: str | None = None
    evidenceAnswerCount: int | None = pydantic.Field(default=None, ge=0)


class SpeechFluencyFeedbackLite(BaseSchemaModel):
    strengths: str
    areasOfImprovement: str
    ratingEmoji: str
    ratingTitle: str
    ratingDescription: str


class NextStepLite(BaseSchemaModel):
    title: str


class FinalTipLite(BaseSchemaModel):
    title: str
    description: str


class ReportEvidenceSummaryLite(BaseSchemaModel):
    totalQuestions: int = pydantic.Field(ge=0)
    savedAnswers: int = pydantic.Field(ge=0)
    usableAnswers: int = pydantic.Field(ge=0)
    evaluatedAnswers: int = pydantic.Field(ge=0)
    minimumRequired: int = pydantic.Field(default=3, ge=1)
    coveragePercentage: int = pydantic.Field(ge=0, le=100)
    confidence: EvidenceConfidence
    statusMessage: str


class SummaryReportResponseLite(BaseSchemaModel):
    """Versioned report contract that never converts missing evidence into a score."""

    reportId: str
    reportVersion: str = "practice-report-v1"
    reportStatus: ReportStatus = "ready"
    generatedAt: str | None = None
    candidateInfo: CandidateInfoLite
    scoreSummary: ScoreSummary | None = None
    evidenceSummary: ReportEvidenceSummaryLite | None = None
    uncertaintyNotice: str | None = None
    questionAnalysis: list[QuestionAnalysisItemLite]
    recommendedPractice: RecommendedPracticeLite | None = None
    speechFluencyFeedback: SpeechFluencyFeedbackLite | None = None
    nextSteps: list[NextStepLite] | None = None
    finalTip: FinalTipLite | None = None

    @pydantic.field_validator("nextSteps", mode="before")
    @classmethod
    def normalize_next_steps(cls, value: Any) -> Any:
        if value is None:
            return None
        normalized: list[NextStepLite] = []
        for item in value:
            if isinstance(item, str):
                normalized.append(NextStepLite(title=item))
            elif isinstance(item, dict):
                normalized.append(NextStepLite(**item))
            elif isinstance(item, NextStepLite):
                normalized.append(item)
        return normalized
