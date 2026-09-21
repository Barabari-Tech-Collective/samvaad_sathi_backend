from __future__ import annotations

import datetime

import pydantic

from src.models.schemas.base import BaseSchemaModel


class AnalyticsV2Filters(BaseSchemaModel):
    start_date: datetime.date | None = None
    end_date: datetime.date | None = None
    role: str | None = None
    difficulty: str | None = None
    college: str | None = None


class PaginationParams(BaseSchemaModel):
    page: int = pydantic.Field(default=1, ge=1)
    limit: int = pydantic.Field(default=20, ge=1, le=100)


class KpiCard(BaseSchemaModel):
    key: str
    label: str
    value: float | int | str | None = None
    unit: str | None = None


class TimeSeriesPoint(BaseSchemaModel):
    date: datetime.date | datetime.datetime | str
    value: float | int


class TimeSeriesResponse(BaseSchemaModel):
    chart_type: str
    points: list[TimeSeriesPoint]


class DistributionBucket(BaseSchemaModel):
    label: str
    count: int


class DistributionResponse(BaseSchemaModel):
    chart_type: str
    buckets: list[DistributionBucket]


class FunnelStage(BaseSchemaModel):
    stage: str
    count: int
    rate: float | None = None


class FunnelResponse(BaseSchemaModel):
    chart_type: str
    stages: list[FunnelStage]


class HeatmapCell(BaseSchemaModel):
    x: str
    y: str
    value: float | int


class HeatmapResponse(BaseSchemaModel):
    chart_type: str
    items: list[HeatmapCell]


class ForecastPoint(BaseSchemaModel):
    date: datetime.date
    predicted_value: float | int
    lower_bound: float | int | None = None
    upper_bound: float | int | None = None


class ForecastResponse(BaseSchemaModel):
    chart_type: str
    points: list[ForecastPoint]


class TablePageResponse(BaseSchemaModel):
    table_type: str
    items: list[dict]
    page: int
    limit: int
    total: int


class DashboardOverviewResponse(BaseSchemaModel):
    kpis: list[KpiCard]


class DashboardTopListResponse(BaseSchemaModel):
    table_type: str
    items: list[dict]


class StudentsSummaryResponse(BaseSchemaModel):
    kpis: list[KpiCard]


class StudentProfileResponse(BaseSchemaModel):
    student_id: int
    name: str
    email: str
    college: str | None = None
    degree: str | None = None
    target_position: str | None = None
    years_experience: float | None = None
    company: str | None = None
    created_at: datetime.datetime
    last_active: datetime.datetime | None = None


class StudentSummaryResponse(BaseSchemaModel):
    student_id: int
    kpis: list[KpiCard]


class StudentScoreHistoryResponse(BaseSchemaModel):
    student_id: int
    chart_type: str
    points: list[dict]


class StudentSkillAveragesResponse(BaseSchemaModel):
    student_id: int
    chart_type: str
    items: list[dict]


class StudentPracticeCompletionResponse(BaseSchemaModel):
    student_id: int
    kpis: list[KpiCard]


class StudentLatestFeedbackResponse(BaseSchemaModel):
    student_id: int
    latest_feedback: str | None = None
    question_attempt_id: int | None = None
    interview_id: int | None = None
    created_at: datetime.datetime | None = None


class CollegesSummaryResponse(BaseSchemaModel):
    kpis: list[KpiCard]


class InterviewsSummaryResponse(BaseSchemaModel):
    kpis: list[KpiCard]


class CollegesFilterResponse(BaseSchemaModel):
    colleges: list[str]


class GlobalSearchResponse(BaseSchemaModel):
    students: list[dict]
    colleges: list[dict]
    interviews: list[dict]
