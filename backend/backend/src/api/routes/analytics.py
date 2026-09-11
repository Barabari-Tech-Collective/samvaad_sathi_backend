from __future__ import annotations

import datetime
import math

import fastapi
import sqlalchemy
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession

from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.interview import Interview
from src.models.db.user import User
from src.models.schemas.analytics import (
    AlertsAnalyticsResponse,
    AnalyticsQueryFilters,
    InterviewAnalyticsResponse,
    ReportEngagementRequest,
    ScoringAnalyticsResponse,
    SegmentAnalyticsResponse,
    StudentAnalyticsResponse,
    SystemAnalyticsResponse,
)
from src.services.analytics import AnalyticsService
from src.services.analytics_events import track_analytics_event


router = fastapi.APIRouter(prefix="/analytics", tags=["analytics"])


_DIFFICULTY_ORDER = {"easy": 0, "medium": 1, "hard": 2, "expert": 3}


def _sort_difficulty_items(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda row: _DIFFICULTY_ORDER.get(str(row.get("difficulty", "")).lower(), 999))


def _zero_fill_metric_nulls(payload):
    metric_hints = (
        "score",
        "duration",
        "time",
        "percent",
        "rate",
        "count",
        "avg",
        "average",
        "delta",
        "completion",
        "retry",
        "wpm",
        "value",
    )
    if isinstance(payload, list):
        return [_zero_fill_metric_nulls(item) for item in payload]
    if isinstance(payload, float) and not math.isfinite(payload):
        return 0
    if isinstance(payload, dict):
        normalized = {}
        for key, value in payload.items():
            cleaned = _zero_fill_metric_nulls(value)
            if cleaned is None and any(hint in key.lower() for hint in metric_hints):
                cleaned = 0
            normalized[key] = cleaned
        return normalized
    return payload


def _filter_payload(
    *,
    start_date: datetime.date | None,
    end_date: datetime.date | None,
    role: str | None,
    difficulty: str | None,
    college: str | None,
) -> AnalyticsQueryFilters:
    return AnalyticsQueryFilters(
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    )


@router.get(
    "/student/{user_id}",
    response_model=StudentAnalyticsResponse,
    status_code=200,
    summary="Student-level analytics",
    description="Returns student performance trends, skill breakdowns, weak areas, compliance, and behavior analytics.",
)
async def get_student_analytics(
    user_id: int,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    # user_id comes straight off the path and was previously never compared
    # against the caller - any student could read any other student's full
    # performance analytics by incrementing the id. Students keep access to
    # their own analytics; anything else requires admin.
    if user_id != current_user.id and not is_admin_user(current_user):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="You may only view your own analytics.",
        )

    service = AnalyticsService(session)
    metrics = await service.get_student_level_analytics(user_id=user_id, start_date=start_date, end_date=end_date)
    return StudentAnalyticsResponse(
        user_id=user_id,
        filters=_filter_payload(start_date=start_date, end_date=end_date, role=None, difficulty=None, college=None),
        metrics=_zero_fill_metric_nulls(metrics),
    )


@router.get(
    "/interview/{interview_id}",
    response_model=InterviewAnalyticsResponse,
    status_code=200,
    summary="Interview/session-level analytics",
    description="Returns analytics and question-level metrics for a specific interview/session.",
)
async def get_interview_analytics(
    interview_id: int,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    # Same gap as /student/{user_id}: interview_id was taken from the path
    # with no ownership check. Students keep access to their own interviews.
    if not is_admin_user(current_user):
        owner_id = (
            await session.execute(
                sqlalchemy.select(Interview.user_id).where(Interview.id == interview_id)
            )
        ).scalar_one_or_none()
        if owner_id is None:
            raise fastapi.HTTPException(status_code=404, detail="Interview not found")
        if owner_id != current_user.id:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_403_FORBIDDEN,
                detail="You may only view analytics for your own interviews.",
            )

    service = AnalyticsService(session)
    metrics = await service.get_interview_level_analytics(interview_id=interview_id)
    if metrics is None:
        raise fastapi.HTTPException(status_code=404, detail="Interview not found")
    return InterviewAnalyticsResponse(interview_id=interview_id, metrics=_zero_fill_metric_nulls(metrics))


@router.get(
    "/segment/role",
    response_model=SegmentAnalyticsResponse,
    status_code=200,
    summary="Role-level segment analytics",
)
async def get_role_segment_analytics(
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    role: str | None = None,
    difficulty: str | None = None,
    college: str | None = None,
    current_user: User = Depends(get_current_admin_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    service = AnalyticsService(session)
    items = await service.get_role_segment_analytics(
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    )
    return SegmentAnalyticsResponse(
        segment="role",
        filters=_filter_payload(start_date=start_date, end_date=end_date, role=role, difficulty=difficulty, college=college),
        items=_zero_fill_metric_nulls(items),
    )


@router.get(
    "/segment/difficulty",
    response_model=SegmentAnalyticsResponse,
    status_code=200,
    summary="Difficulty-level segment analytics",
)
async def get_difficulty_segment_analytics(
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    role: str | None = None,
    difficulty: str | None = None,
    college: str | None = None,
    current_user: User = Depends(get_current_admin_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    service = AnalyticsService(session)
    items = await service.get_difficulty_segment_analytics(
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    )
    items = _sort_difficulty_items(items)
    return SegmentAnalyticsResponse(
        segment="difficulty",
        filters=_filter_payload(start_date=start_date, end_date=end_date, role=role, difficulty=difficulty, college=college),
        items=_zero_fill_metric_nulls(items),
    )


@router.get(
    "/segment/college",
    response_model=SegmentAnalyticsResponse,
    status_code=200,
    summary="College-level segment analytics",
)
async def get_college_segment_analytics(
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    role: str | None = None,
    difficulty: str | None = None,
    college: str | None = None,
    current_user: User = Depends(get_current_admin_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    service = AnalyticsService(session)
    items = await service.get_college_segment_analytics(
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    )
    return SegmentAnalyticsResponse(
        segment="college",
        filters=_filter_payload(start_date=start_date, end_date=end_date, role=role, difficulty=difficulty, college=college),
        items=_zero_fill_metric_nulls(items),
    )


@router.get(
    "/system",
    response_model=SystemAnalyticsResponse,
    status_code=200,
    summary="System/product-level analytics",
)
async def get_system_analytics(
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    role: str | None = None,
    difficulty: str | None = None,
    college: str | None = None,
    current_user: User = Depends(get_current_admin_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    service = AnalyticsService(session)
    metrics = await service.get_system_analytics(
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    )
    return SystemAnalyticsResponse(
        filters=_filter_payload(start_date=start_date, end_date=end_date, role=role, difficulty=difficulty, college=college),
        metrics=_zero_fill_metric_nulls(metrics),
    )


@router.get(
    "/scoring",
    response_model=ScoringAnalyticsResponse,
    status_code=200,
    summary="Scoring quality analytics",
)
async def get_scoring_analytics(
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    role: str | None = None,
    difficulty: str | None = None,
    college: str | None = None,
    current_user: User = Depends(get_current_admin_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    service = AnalyticsService(session)
    metrics = await service.get_scoring_analytics(
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    )
    return ScoringAnalyticsResponse(
        filters=_filter_payload(start_date=start_date, end_date=end_date, role=role, difficulty=difficulty, college=college),
        metrics=_zero_fill_metric_nulls(metrics),
    )


@router.get(
    "/alerts",
    response_model=AlertsAnalyticsResponse,
    status_code=200,
    summary="Computed analytics alerts",
)
async def get_analytics_alerts(
    user_id: int | None = None,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    current_user: User = Depends(get_current_admin_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    service = AnalyticsService(session)
    alerts = await service.get_alerts(user_id=user_id, start_date=start_date, end_date=end_date)
    return AlertsAnalyticsResponse(
        filters=_filter_payload(start_date=start_date, end_date=end_date, role=None, difficulty=None, college=None),
        student_alerts=_zero_fill_metric_nulls(alerts["student_alerts"]),
        system_alerts=_zero_fill_metric_nulls(alerts["system_alerts"]),
    )


@router.post(
    "/report-engagement",
    status_code=200,
    summary="Track report engagement",
    description="Stores report engagement signals such as time spent and recommendation clicks.",
)
async def track_report_engagement(
    payload: ReportEngagementRequest,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    await track_analytics_event(
        session,
        event_type="report_engagement",
        user_id=current_user.id,
        interview_id=payload.interview_id,
        event_data={
            "time_spent_seconds": payload.time_spent_seconds,
            "recommendation_clicks": payload.recommendation_clicks,
            "report_type": payload.report_type,
        },
    )
    return {"status": "ok"}
