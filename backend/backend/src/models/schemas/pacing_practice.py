"""Schemas for speech pacing practice endpoints."""

import datetime
from uuid import UUID
import pydantic
from typing import Optional

from src.models.schemas.base import BaseSchemaModel


class PacingPracticeSessionCreateRequest(BaseSchemaModel):
    """Request body for creating a new pacing practice session."""
    client_request_id: UUID | None = None
    level: int = pydantic.Field(
        ...,
        ge=1,
        le=3,
        description="Difficulty level: 1 = Sentence Control, 2 = Paragraph Fluency, 3 = Interview Mastery",
    )


class PacingPracticeSessionResponse(BaseSchemaModel):
    """Response returned when a new pacing practice session is created."""
    session_id: int
    level: int
    level_name: str
    prompt_text: str
    status: str
    created_at: datetime.datetime


class PacingAnalysisMetric(BaseSchemaModel):
    """A single measured metric with its status and contextual feedback."""
    value: float
    ideal_range: str
    status: str          # "Good" | "Needs Adjustment"
    feedback: str


class PauseDistributionMetric(BaseSchemaModel):
    """Rich pause distribution data from the linguistic 4-component scorer."""
    score: int                       # 0-100 pause-specific score
    status: str                      # "Good" | "Average" | "Needs Adjustment"
    feedback: str
    avg_words_per_pause: float
    total_pauses: int
    expected_pauses: float
    mandatory_pause_count: int
    mandatory_pauses_hit: int
    mandatory_pauses_missed: int
    comma_pauses_missed: int
    mandatory_covered: bool
    placement_accuracy: float        # % component score
    mandatory_compliance: float      # % component score
    segment_accuracy: float          # % component score
    penalty_pct: float               # % penalty applied


class FillerWordsMetric(BaseSchemaModel):
    """Filler word detection results."""
    count: int
    total_words: int
    filler_ratio: float
    status: str                      # "Good" | "Average" | "Needs Adjustment"
    suggestion: str
    fillers_found: list[str]


class Level3SpeechSpeedMetric(BaseSchemaModel):
    score: int
    status: str
    wpm: float
    ideal_range: str
    feedback: str


class Level3SpeechConsistencyMetric(BaseSchemaModel):
    score: int
    status: str
    variance_wpm: float
    start_wpm: float
    middle_wpm: float
    end_wpm: float
    feedback: str


class Level3PauseDistributionMetric(BaseSchemaModel):
    score: int
    status: str
    total_pauses: int
    micro_pause_pct: float
    thinking_pause_pct: float
    long_pause_pct: float
    feedback: str


class Level3SentenceVariationMetric(BaseSchemaModel):
    score: int
    status: str
    sentence_count: int
    std_dev_words: float
    variation_level: str
    feedback: str


class Level3DurationMetric(BaseSchemaModel):
    score: int
    status: str
    actual_seconds: float
    expected_min_seconds: float
    expected_max_seconds: float
    feedback: str


class Level3EnergyMetric(BaseSchemaModel):
    score: int
    status: str
    pitch_variation: float
    volume_variation: float
    feedback: str


class Level3ConsistencyMetric(BaseSchemaModel):
    score: int
    status: str
    fluctuation_index: float
    feedback: str


class Level3DeliveryControlGroup(BaseSchemaModel):
    score: int
    status: str
    speech_speed: Level3SpeechSpeedMetric
    speech_consistency: Level3SpeechConsistencyMetric


class Level3ClarityGroup(BaseSchemaModel):
    score: int
    status: str
    pause_distribution: Level3PauseDistributionMetric
    sentence_variation: Level3SentenceVariationMetric


class Level3FluencyGroup(BaseSchemaModel):
    score: int
    status: str
    filler_words: FillerWordsMetric


class Level3InterviewQualityGroup(BaseSchemaModel):
    score: int
    status: str
    response_duration: Level3DurationMetric
    energy_level: Level3EnergyMetric
    consistency: Level3ConsistencyMetric


class Level3PacingReport(BaseSchemaModel):
    overall_score: int
    overall_status: str
    delivery_control: Level3DeliveryControlGroup
    clarity: Level3ClarityGroup
    fluency: Level3FluencyGroup
    interview_quality: Level3InterviewQualityGroup


class PacingPracticeSubmitResponse(BaseSchemaModel):
    """Response returned after audio is submitted and analysed."""
    session_id: int
    level: int
    score: int           # 0-100
    score_label: str     # e.g. "Good Progress! Keep Practicing"
    speech_speed: PacingAnalysisMetric
    pause_distribution: PauseDistributionMetric
    filler_words: FillerWordsMetric
    level3_report: Optional[Level3PacingReport] = None
    level_unlocked: Optional[int] = None   # 2 or 3 if this attempt unlocked a new level


class PacingLevelStatus(BaseSchemaModel):
    """Status of a single pacing practice level for the dashboard."""
    level: int
    name: str
    description: str
    status: str              # "locked" | "in_progress" | "complete"
    best_score: Optional[int] = None
    unlock_threshold: int    # score needed to unlock the NEXT level
    unlock_message: str      # e.g. "Unlock level-2 at 90%"


class PacingLevelsResponse(BaseSchemaModel):
    """Full dashboard data: all three levels + overall readiness."""
    levels: list[PacingLevelStatus]
    overall_readiness: int   # 0-100 percentage


class PacingPracticeStatusResponse(BaseSchemaModel):
    """Quick status indicating if user has completed pacing practice before."""
    has_practiced: bool


class PacingPracticeSessionDetailResponse(BaseSchemaModel):
    """Full details of a completed pacing practice session."""
    session_id: int
    level: int
    level_name: str
    prompt_text: str
    status: str
    transcript: Optional[str] = None
    score: Optional[int] = None
    speech_speed: Optional[PacingAnalysisMetric] = None
    pause_distribution: Optional[PauseDistributionMetric] = None
    filler_words: Optional[FillerWordsMetric] = None
    level3_report: Optional[Level3PacingReport] = None
    analysis_result: Optional[dict] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
