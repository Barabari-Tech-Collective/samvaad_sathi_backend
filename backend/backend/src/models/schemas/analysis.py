"""Analysis request and response models for aggregated analysis endpoints."""

import pydantic
from typing import List, Dict, Any

from src.models.schemas.base import BaseSchemaModel


class CompleteAnalysisRequest(BaseSchemaModel):
    """Request model for complete analysis endpoint"""
    question_attempt_id: pydantic.StrictInt = pydantic.Field(gt=0, description="ID of the question attempt to analyze (must be > 0, no string coercion)")
    answer_version: pydantic.StrictInt | None = pydantic.Field(
        default=None,
        gt=0,
        description="Answer version acknowledged by the save endpoint; prevents stale AI results",
    )
    analysis_types: List[str] = pydantic.Field(
        default=["domain", "communication", "pace", "pause"],
        description="List of analysis types to perform. Available: domain, communication, pace, pause"
    )

    @pydantic.field_validator("analysis_types")
    @classmethod
    def validate_analysis_types(cls, v: List[str]) -> List[str]:
        allowed_types = {"domain", "communication", "pace", "pause"}
        invalid_types = set(v) - allowed_types
        if invalid_types:
            raise ValueError(f"Invalid analysis types: {invalid_types}. Allowed: {allowed_types}")
        return v

    # OpenAPI example
    model_config = BaseSchemaModel.model_config.copy()
    model_config["json_schema_extra"] = {
        "examples": [
            {
                "questionAttemptId": 1001,
                "answerVersion": 1,
                "analysisTypes": ["domain", "communication", "pace", "pause"]
            }
        ]
    }


class DomainAnalysisRequest(BaseSchemaModel):
    """Request model for domain analysis endpoint"""
    question_attempt_id: pydantic.StrictInt = pydantic.Field(gt=0, description="ID of the question attempt to analyze (must be > 0, no string coercion)")
    job_role: str | None = pydantic.Field(default=None, description="Optional job role/title to guide evaluation")
    override_transcription: str | None = pydantic.Field(default=None, description="Optional transcription text override")


class CommunicationAnalysisRequest(BaseSchemaModel):
    """Request model for communication analysis endpoint"""
    question_attempt_id: pydantic.StrictInt = pydantic.Field(gt=0, description="ID of the question attempt to analyze (must be > 0, no string coercion)")
    job_role: str | None = pydantic.Field(default=None, description="Optional job role/title to guide evaluation")
    override_transcription: str | None = pydantic.Field(default=None, description="Optional transcription text override")


class PaceAnalysisRequest(BaseSchemaModel):
    """Request model for pace analysis endpoint"""
    question_attempt_id: pydantic.StrictInt = pydantic.Field(
        gt=0, description="ID of the question attempt to analyze (must be > 0, no string coercion)"
    )


class PauseAnalysisRequest(BaseSchemaModel):
    """Request model for pause analysis endpoint"""
    question_attempt_id: pydantic.StrictInt = pydantic.Field(
        gt=0, description="ID of the question attempt to analyze (must be > 0, no string coercion)"
    )


class AnalysisMetadata(BaseSchemaModel):
    """Metadata about the analysis process"""
    total_latency_ms: int = pydantic.Field(description="Total time taken for all analyses in milliseconds")
    completed_analyses: List[str] = pydantic.Field(description="List of successfully completed analysis types")
    failed_analyses: List[str] = pydantic.Field(description="List of failed analysis types")
    partial_failure: bool = pydantic.Field(description="Whether some analyses failed while others succeeded")


class AggregatedAnalysis(BaseSchemaModel):
    """Container for all analysis results"""
    domain: Dict[str, Any] | None = pydantic.Field(default=None, description="Domain knowledge analysis results")
    communication: Dict[str, Any] | None = pydantic.Field(default=None, description="Communication quality analysis results")
    pace: Dict[str, Any] | None = pydantic.Field(default=None, description="Speaking pace analysis results")
    pause: Dict[str, Any] | None = pydantic.Field(default=None, description="Pause pattern analysis results")


class CompleteAnalysisResponse(BaseSchemaModel):
    """Response model for complete analysis endpoint"""
    question_attempt_id: int = pydantic.Field(description="ID of the analyzed question attempt")
    analysis_complete: bool = pydantic.Field(description="Whether all requested analyses completed successfully")
    aggregated_analysis: AggregatedAnalysis = pydantic.Field(description="Combined analysis results from all types")
    metadata: AnalysisMetadata = pydantic.Field(description="Analysis process metadata and timing information")
    saved: bool = pydantic.Field(description="Whether the aggregated analysis was successfully saved to database")
    save_error: str | None = pydantic.Field(default=None, description="Error message if database save failed")
    message: str = pydantic.Field(description="Human-friendly status message about the analysis process")

    # OpenAPI example
    model_config = BaseSchemaModel.model_config.copy()
    model_config["json_schema_extra"] = {
        "examples": [
            {
                "questionAttemptId": 1001,
                "analysisComplete": True,
                "aggregatedAnalysis": {
                    "domain": {
                        "domainScore": 84.5,
                        "knowledgeAreas": ["ACID", "Indexing"],
                        "strengths": ["Clear concepts"],
                        "improvements": ["More examples"]
                    },
                    "communication": {
                        "communicationScore": 76.0,
                        "clarityScore": 74.0,
                        "vocabularyScore": 78.0,
                        "grammarScore": 80.0,
                        "structureScore": 75.0,
                        "recommendations": ["Shorter sentences"]
                    },
                    "pace": {
                        "paceScore": 88.0,
                        "wordsPerMinute": 145.0,
                        "paceFeedback": "Optimal pace",
                        "paceCategory": "optimal",
                        "recommendations": []
                    },
                    "pause": {
                        "pauseScore": 72.0,
                        "totalPauseDuration": 12.5,
                        "pauseCount": 8,
                        "averagePauseDuration": 1.56,
                        "longestPauseDuration": 3.2,
                        "pauseFeedback": "Slightly frequent pauses",
                        "recommendations": ["Plan next point before speaking"]
                    }
                },
                "metadata": {
                    "totalLatencyMs": 2310,
                    "completedAnalyses": ["domain", "communication", "pace", "pause"],
                    "failedAnalyses": [],
                    "partialFailure": False
                },
                "saved": True,
                "saveError": None,
                "message": "Analysis completed and saved"
            }
        ]
    }


# Individual analysis response models for internal use
class DomainAnalysisResponse(BaseSchemaModel):
    """Response from domain analysis endpoint"""
    question_attempt_id: int
    domain_score: float = pydantic.Field(ge=0.0, le=100.0)
    domain_feedback: str
    knowledge_areas: List[str]
    strengths: List[str]
    improvements: List[str]


class CommunicationAnalysisResponse(BaseSchemaModel):
    """Response from communication analysis endpoint"""
    question_attempt_id: int
    communication_score: float = pydantic.Field(ge=0.0, le=100.0)
    clarity_score: float = pydantic.Field(ge=0.0, le=100.0)
    vocabulary_score: float = pydantic.Field(ge=0.0, le=100.0)
    grammar_score: float = pydantic.Field(ge=0.0, le=100.0)
    structure_score: float = pydantic.Field(ge=0.0, le=100.0)
    communication_feedback: str
    strengths: List[str] = pydantic.Field(default_factory=list)
    improvements: List[str] = pydantic.Field(default_factory=list)
    recommendations: List[str] = pydantic.Field(default_factory=list)  # For backward compatibility


class PaceAnalysisResponse(BaseSchemaModel):
    """Response from pace analysis endpoint"""
    question_attempt_id: int
    pace_score: float = pydantic.Field(ge=0.0, le=100.0)
    words_per_minute: float
    pace_feedback: str
    pace_category: str  # "too_slow", "optimal", "too_fast"
    recommendations: List[str]

class Distribution(pydantic.BaseModel):
    """Pause type distribution percentages"""
    long: str
    rushed: str
    strategic: str
    normal: str

class PauseAnalysisResponse(BaseSchemaModel):
    """Response from pause analysis endpoint"""
    question_attempt_id: int
    pause_score: float = pydantic.Field(ge=0.0, le=100.0)
    overview:str
    details:List[str]
    distribution: Distribution
    actionable_feedback: str    
