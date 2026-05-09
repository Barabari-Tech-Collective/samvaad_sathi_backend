"""Schemas for the interview Summary Report (independent of Final Report).

This response is restructured to provide a cleaner, more actionable format:
- reportId: Unique identifier for the report
- candidateInfo: Basic interview metadata
- scoreSummary: Numeric scores with max values and percentages
- overallFeedback: Focused feedback on speech fluency with actionable steps
- questionAnalysis: Per-question breakdown with feedback (null if not attempted)

It is generated directly from per-question analyses and does not depend on
the Final Report models.
"""

from __future__ import annotations

import pydantic
from typing import List, Dict, Any

from src.models.schemas.base import BaseSchemaModel


class SummaryReportRequest(BaseSchemaModel):
    """Input to generate the summary report for a given interview."""

    interview_id: pydantic.StrictInt = pydantic.Field(gt=0)

    model_config = BaseSchemaModel.model_config.copy()
    model_config["json_schema_extra"] = {"examples": [{"interviewId": 606}]}


# New structure models
class CandidateInfo(BaseSchemaModel):
    """Basic candidate and interview information."""
    name: str | None = pydantic.Field(default=None, description="Candidate name if available")
    interviewDate: str = pydantic.Field(description="ISO date string for interview")
    roleTopic: str = pydantic.Field(description="Interview track/role (e.g., 'Frontend Development')")


class ScoreCriteria(BaseSchemaModel):
    """Individual criterion scores for knowledge competence."""
    accuracy: int = pydantic.Field(default=0, ge=0, le=5, description="Accuracy score (0-5)")
    depth: int = pydantic.Field(default=0, ge=0, le=5, description="Depth score (0-5)")
    relevance: int = pydantic.Field(default=0, ge=0, le=5, description="Relevance score (0-5)")
    examples: int = pydantic.Field(default=0, ge=0, le=5, description="Examples score (0-5)")
    terminology: int = pydantic.Field(default=0, ge=0, le=5, description="Terminology score (0-5)")


class KnowledgeCompetenceScore(BaseSchemaModel):
    """Knowledge competence scoring with numeric values."""
    score: int = pydantic.Field(ge=0, le=25, description="Total knowledge score")
    maxScore: int = pydantic.Field(default=25, ge=0, description="Maximum possible score")
    average: float = pydantic.Field(ge=0.0, le=5.0, description="Average score per criterion")
    maxAverage: float = pydantic.Field(default=5.0, ge=0.0, description="Maximum average")
    percentage: int = pydantic.Field(ge=0, le=100, description="Score as percentage")
    criteria: ScoreCriteria


class SpeechCriteria(BaseSchemaModel):
    """Individual criterion scores for speech and structure."""
    fluency: int = pydantic.Field(default=0, ge=0, le=5, description="Fluency score (0-5)")
    structure: int = pydantic.Field(default=0, ge=0, le=5, description="Structure score (0-5)")
    pacing: int = pydantic.Field(default=0, ge=0, le=5, description="Pacing score (0-5)")
    grammar: int = pydantic.Field(default=0, ge=0, le=5, description="Grammar score (0-5)")


class SpeechAndStructureScore(BaseSchemaModel):
    """Speech and structure scoring with numeric values."""
    score: int = pydantic.Field(ge=0, le=20, description="Total speech score")
    maxScore: int = pydantic.Field(default=20, ge=0, description="Maximum possible score")
    average: float = pydantic.Field(ge=0.0, le=5.0, description="Average score per criterion")
    maxAverage: float = pydantic.Field(default=5.0, ge=0.0, description="Maximum average")
    percentage: int = pydantic.Field(ge=0, le=100, description="Score as percentage")
    criteria: SpeechCriteria


class ScoreSummary(BaseSchemaModel):
    """Overall score summary with knowledge and speech metrics."""
    knowledgeCompetence: KnowledgeCompetenceScore | None = None
    speechAndStructure: SpeechAndStructureScore


class ActionableStep(BaseSchemaModel):
    """Individual actionable step with title and description."""
    title: str = pydantic.Field(description="Step title (e.g., 'Fluency Drills')")
    description: str = pydantic.Field(description="Detailed step description")


class SpeechFluencyFeedback(BaseSchemaModel):
    """Speech fluency feedback with strengths, improvements, and steps."""
    strengths: List[str] = pydantic.Field(default_factory=list, description="Speech strengths")
    areasOfImprovement: List[str] = pydantic.Field(default_factory=list, description="Areas to improve")
    actionableSteps: List[ActionableStep] = pydantic.Field(default_factory=list, description="Concrete action items")


class OverallFeedback(BaseSchemaModel):
    """Overall feedback focused on speech fluency."""
    speechFluency: SpeechFluencyFeedback


class QuestionFeedbackSection(BaseSchemaModel):
    """Feedback section for a question with knowledge and speech categories."""
    knowledgeRelated: QuestionFeedbackSubsection
    


class QuestionFeedbackSubsection(BaseSchemaModel):
    """Subsection containing strengths, improvements, and insights."""
    strengths: List[str] = pydantic.Field(default_factory=list)
    areasOfImprovement: List[str] = pydantic.Field(default_factory=list)
    actionableInsights: List[ActionableStep] = pydantic.Field(default_factory=list)


class QuestionFeedback(BaseSchemaModel):
    """Complete feedback for a single question."""
    knowledgeRelated: QuestionFeedbackSubsection
    # Note: speechRelated removed per new structure - only knowledge feedback per question


class QuestionAnalysisItem(BaseSchemaModel):
    """Individual question analysis with scores and feedback."""
    id: int = pydantic.Field(description="Question ID")
    totalQuestions: int = pydantic.Field(description="Total number of questions in interview")
    type: str = pydantic.Field(description="Question type (e.g., 'Technical question')")
    question: str = pydantic.Field(description="Question text")
    feedback: QuestionFeedback | None = pydantic.Field(default=None, description="Feedback (null if not attempted)")


class SummaryReportResponse(BaseSchemaModel):
    """Restructured summary report response matching new format."""
    reportId: str = pydantic.Field(description="Unique report identifier (UUID)")
    candidateInfo: CandidateInfo
    scoreSummary: ScoreSummary
    overallFeedback: OverallFeedback
    questionAnalysis: List[QuestionAnalysisItem]


# Legacy models for backward compatibility (deprecated)
class KnowledgeCompetenceBreakdown(BaseSchemaModel):
    accuracy: float | None = pydantic.Field(default=None, ge=0.0, le=5.0)
    depth: float | None = pydantic.Field(default=None, ge=0.0, le=5.0)
    coverage: float | None = pydantic.Field(default=None, ge=0.0, le=5.0)
    relevance: float | None = pydantic.Field(default=None, ge=0.0, le=5.0)


class SpeechStructureBreakdown(BaseSchemaModel):
    pacing: float | None = pydantic.Field(default=None, ge=0.0, le=5.0)
    structure: float | None = pydantic.Field(default=None, ge=0.0, le=5.0)
    pauses: float | None = pydantic.Field(default=None, ge=0.0, le=5.0)
    grammar: float | None = pydantic.Field(default=None, ge=0.0, le=5.0)


class OverallScoreKnowledgeCompetence(BaseSchemaModel):
    average5pt: float | None = pydantic.Field(default=None, ge=0.0, le=5.0)
    averagePct: float | None = pydantic.Field(default=None, ge=0.0, le=100.0)
    breakdown: KnowledgeCompetenceBreakdown | None = None


class OverallScoreSpeechStructure(BaseSchemaModel):
    average5pt: float | None = pydantic.Field(default=None, ge=0.0, le=5.0)
    averagePct: float | None = pydantic.Field(default=None, ge=0.0, le=100.0)
    breakdown: SpeechStructureBreakdown | None = None


class SummaryMetrics(BaseSchemaModel):
    knowledgeCompetence: OverallScoreKnowledgeCompetence
    speechStructure: OverallScoreSpeechStructure


class SummarySectionGroup(BaseSchemaModel):
    label: str = pydantic.Field(description="Section label, e.g., 'Knowledge-Related'")
    items: List[str] = pydantic.Field(default_factory=list, description="Bullet points for this label")


class SummarySection(BaseSchemaModel):
    heading: str = pydantic.Field(description="Primary heading, e.g., 'Strengths'")
    subtitle: str | None = pydantic.Field(default=None, description="Optional subtitle such as 'For Knowledge Development'")
    groups: List[SummarySectionGroup] = pydantic.Field(default_factory=list, description="Labeled bullet point groups")


class FinalSummarySection(BaseSchemaModel):
    knowledgeRelated: List[str] = pydantic.Field(default_factory=list)
    speechFluencyRelated: List[str] = pydantic.Field(default_factory=list)


class FinalSummary(BaseSchemaModel):
    strengths: FinalSummarySection
    areasOfImprovement: FinalSummarySection


class KnowledgeDevelopmentSteps(BaseSchemaModel):
    targetedConceptReinforcement: List[str] = pydantic.Field(default_factory=list)
    examplePractice: List[str] = pydantic.Field(default_factory=list)
    conceptualDepth: List[str] = pydantic.Field(default_factory=list)


class SpeechStructureFluencySteps(BaseSchemaModel):
    fluencyDrills: List[str] = pydantic.Field(default_factory=list)
    grammarPractice: List[str] = pydantic.Field(default_factory=list)
    structureFramework: List[str] = pydantic.Field(default_factory=list)


class ActionableSteps(BaseSchemaModel):
    knowledgeDevelopment: KnowledgeDevelopmentSteps
    speechStructureFluency: SpeechStructureFluencySteps


class ReportMetadata(BaseSchemaModel):
    totalQuestions: int = 0
    usedQuestions: int = 0
    model: str | None = None
    latencyMs: int | None = None
    generatedAt: str | None = None
    resumeUsed: bool | None = None


class PerQuestionItem(BaseSchemaModel):
    questionId: int  # Always present - from InterviewQuestion.id
    questionAttemptId: int | None = None  # None for unattempted questions
    questionText: str | None = None
    questionCategory: str | None = None  # tech | tech_allied | behavioral
    keyTakeaways: List[str] = pydantic.Field(default_factory=list)
    knowledgeScorePct: float | None = pydantic.Field(default=None, ge=0.0, le=100.0)
    speechScorePct: float | None = pydantic.Field(default=None, ge=0.0, le=100.0)


class PerQuestionAnalysis(PerQuestionItem):
    strengths: SummarySection
    areasOfImprovement: SummarySection
    actionableInsights: SummarySection


class TopicHighlights(BaseSchemaModel):
    strengthsTopics: List[str] = pydantic.Field(default_factory=list)
    improvementTopics: List[str] = pydantic.Field(default_factory=list)


class SummaryReportListItem(BaseSchemaModel):
    """Individual summary report item for the list endpoint."""
    interview_id: int = pydantic.Field(description="Unique identifier for the interview")
    track: str = pydantic.Field(description="Interview track (e.g., 'javascript developer')")
    difficulty: str = pydantic.Field(description="Interview difficulty level")
    created_at: str = pydantic.Field(description="When the summary report was created")
    overall_score: float | None = pydantic.Field(default=None, ge=0.0, le=100.0, description="Overall score percentage")
    report: SummaryReportResponse = pydantic.Field(description="Complete summary report data")


class SummaryReportsListResponse(BaseSchemaModel):
    """Response for the summary reports list endpoint."""
    items: List[SummaryReportListItem] = pydantic.Field(description="List of summary reports")
    total_count: int = pydantic.Field(description="Total number of summary reports found")
    limit: int = pydantic.Field(description="Maximum number of items requested")



class SummaryReportListItem(BaseSchemaModel):
    """Individual summary report item for the list endpoint."""
    interview_id: int = pydantic.Field(description="Unique identifier for the interview")
    track: str = pydantic.Field(description="Interview track (e.g., 'javascript developer')")
    difficulty: str = pydantic.Field(description="Interview difficulty level")
    created_at: str = pydantic.Field(description="When the summary report was created")
    overall_score: float | None = pydantic.Field(default=None, ge=0.0, le=100.0, description="Knowledge competence percentage")
    report: SummaryReportResponse = pydantic.Field(description="Complete summary report data")


class SummaryReportsListResponse(BaseSchemaModel):
    """Response for the summary reports list endpoint."""
    items: List[SummaryReportListItem] = pydantic.Field(description="List of summary reports")
    total_count: int = pydantic.Field(description="Total number of summary reports found")
    limit: int = pydantic.Field(description="Maximum number of items requested")
