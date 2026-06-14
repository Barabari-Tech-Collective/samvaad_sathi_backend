from __future__ import annotations

import datetime
import math
from collections import defaultdict
from typing import Any

import fastapi
import sqlalchemy
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.interview import Interview
from src.models.db.interview_question import InterviewQuestion
from src.models.db.summary_report import SummaryReport
from src.models.db.question_attempt import QuestionAttempt
from src.models.db.report import Report
from src.models.db.user import User
from src.models.db.analytics_event import AnalyticsEvent
from src.models.schemas.analytics_v2 import (
    CollegesFilterResponse,
    CollegesSummaryResponse,
    DashboardOverviewResponse,
    DashboardTopListResponse,
    DistributionBucket,
    DistributionResponse,
    ForecastPoint,
    ForecastResponse,
    FunnelResponse,
    FunnelStage,
    GlobalSearchResponse,
    HeatmapCell,
    HeatmapResponse,
    InterviewsSummaryResponse,
    KpiCard,
    StudentLatestFeedbackResponse,
    StudentPracticeCompletionResponse,
    StudentProfileResponse,
    StudentScoreHistoryResponse,
    StudentSkillAveragesResponse,
    StudentSummaryResponse,
    StudentsSummaryResponse,
    TablePageResponse,
    TimeSeriesPoint,
    TimeSeriesResponse,
)
from src.services.analytics import AnalyticsService


COMMON_ERROR_RESPONSES = {
    401: {"description": "Authentication required or invalid bearer token."},
    500: {"description": "Unexpected server error while computing analytics."},
}


router = fastapi.APIRouter(prefix="/v2/analytics", tags=["analytics-v2"], responses=COMMON_ERROR_RESPONSES)


_DIFFICULTY_ORDER = {"easy": 0, "medium": 1, "hard": 2, "expert": 3}


def _apply_interview_filters(
    stmt: sqlalchemy.sql.Select,
    *,
    start_date: datetime.date | None,
    end_date: datetime.date | None,
    role: str | None,
    difficulty: str | None,
    college: str | None,
) -> sqlalchemy.sql.Select:
    filtered_stmt = stmt
    if college:
        filtered_stmt = filtered_stmt.join(User, User.id == Interview.user_id).where(User.university == college)
    if role:
        filtered_stmt = filtered_stmt.where(Interview.track == role)
    if difficulty:
        filtered_stmt = filtered_stmt.where(Interview.difficulty == difficulty)
    if start_date is not None:
        filtered_stmt = filtered_stmt.where(
            Interview.created_at >= datetime.datetime.combine(start_date, datetime.time.min, tzinfo=datetime.timezone.utc)
        )
    if end_date is not None:
        filtered_stmt = filtered_stmt.where(
            Interview.created_at <= datetime.datetime.combine(end_date, datetime.time.max, tzinfo=datetime.timezone.utc)
        )
    return filtered_stmt


def _safe_percent(numerator: float | int, denominator: float | int) -> float:
    if denominator == 0:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100.0, 2)


def _extract_distribution_buckets(raw_distribution: list[dict[str, Any]]) -> list[DistributionBucket]:
    buckets: list[DistributionBucket] = []
    for item in raw_distribution:
        label = f"{int(item.get('start', 0))}-{int(item.get('end', 0))}"
        buckets.append(DistributionBucket(label=label, count=int(item.get("count", 0))))
    return buckets


def _safe_avg(values: list[float | int | None]) -> float | None:
    clean = [float(v) for v in values if isinstance(v, (float, int))]
    if not clean:
        return 0.0
    return round(sum(clean) / len(clean), 2)


def _metric_or_zero(value: Any, *, digits: int | None = None) -> int | float:
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return 0
        if digits is not None:
            return round(float(value), digits)
        return float(value)
    return 0


def _zero_fill_metric_nulls(payload: Any) -> Any:
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
        normalized: dict[str, Any] = {}
        for key, value in payload.items():
            cleaned_value = _zero_fill_metric_nulls(value)
            if cleaned_value is None and any(hint in key.lower() for hint in metric_hints):
                cleaned_value = 0
            normalized[key] = cleaned_value
        return normalized
    return payload


def _sort_difficulty_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda row: _DIFFICULTY_ORDER.get(str(row.get("difficulty", "")).lower(), 999))


def _to_date(value: datetime.date | datetime.datetime | None) -> datetime.date | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    return value


@router.get(
    "/dashboard/overview",
    response_model=DashboardOverviewResponse,
    status_code=200,
    summary="Dashboard overview KPIs",
    description="Returns KPI cards for students, activity, interview volume, average score, and completion metrics."
    " Supports optional date, role, difficulty, and college filters.",
)
async def get_dashboard_overview(
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    role: str | None = None,
    difficulty: str | None = None,
    college: str | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    system_metrics = await service.get_system_analytics(
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    )

    interviews_count_stmt = _apply_interview_filters(
        sqlalchemy.select(sqlalchemy.func.count(Interview.id)),
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    )
    total_interviews = int((await session.execute(interviews_count_stmt)).scalar() or 0)
    completed_interviews_stmt = _apply_interview_filters(
        sqlalchemy.select(sqlalchemy.func.count(Interview.id)).where(Interview.status == "completed"),
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    )
    completed_interviews = int((await session.execute(completed_interviews_stmt)).scalar() or 0)

    overview = system_metrics.get("overview", {})
    kpis = [
        KpiCard(key="total_students", label="Total Students", value=int(overview.get("total_users", 0))),
        KpiCard(key="active_users", label="Active Users", value=int(overview.get("active_users_30d", 0))),
        KpiCard(key="total_interviews", label="Total Interviews", value=total_interviews),
        KpiCard(key="average_score", label="Average Score", value=overview.get("avg_score"), unit="score"),
        KpiCard(key="improvement_percent", label="Improvement %", value=overview.get("improvement_percent"), unit="percent"),
        KpiCard(
            key="completion_rate",
            label="Completion Rate",
            value=_safe_percent(completed_interviews, total_interviews),
            unit="percent",
        ),
    ]
    return DashboardOverviewResponse(kpis=kpis)


@router.get(
    "/dashboard/interviews-per-day",
    response_model=TimeSeriesResponse,
    status_code=200,
    summary="Interviews per day trend",
    description="Reasoning: helps identify usage spikes and troughs over time for operational monitoring."
    " Output: time-series points with date and interview count.",
)
async def get_dashboard_interviews_per_day(
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    role: str | None = None,
    difficulty: str | None = None,
    college: str | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    stmt = _apply_interview_filters(
        sqlalchemy.select(sqlalchemy.func.date(Interview.created_at), sqlalchemy.func.count(Interview.id)).group_by(
            sqlalchemy.func.date(Interview.created_at)
        ),
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    ).order_by(sqlalchemy.func.date(Interview.created_at).asc())
    rows = list((await session.execute(stmt)).all())
    points = [TimeSeriesPoint(date=row[0], value=int(row[1])) for row in rows if row[0] is not None]
    return TimeSeriesResponse(chart_type="line", points=points)


@router.get("/dashboard/active-users-trend", response_model=TimeSeriesResponse, status_code=200, summary="Active users trend", description="Reasoning: tracks daily engagement breadth across unique users. Output: date-wise active-user time-series points.")
async def get_dashboard_active_users_trend(
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    role: str | None = None,
    difficulty: str | None = None,
    college: str | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    ) -> TimeSeriesResponse:
    del current_user
    stmt = _apply_interview_filters(
        sqlalchemy.select(sqlalchemy.func.date(Interview.created_at), sqlalchemy.func.count(sqlalchemy.distinct(Interview.user_id))).group_by(
            sqlalchemy.func.date(Interview.created_at)
        ),
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    ).order_by(sqlalchemy.func.date(Interview.created_at).asc())
    rows = list((await session.execute(stmt)).all())
    points = [TimeSeriesPoint(date=row[0], value=int(row[1])) for row in rows if row[0] is not None]
    return TimeSeriesResponse(chart_type="area", points=points)


@router.get("/dashboard/top-roles", response_model=DashboardTopListResponse, status_code=200, summary="Top roles by interview volume", description="Reasoning: identifies highest-demand roles for planning content and resources. Output: top-list role metrics.")
async def get_dashboard_top_roles(
    limit: int = fastapi.Query(default=5, ge=1, le=20),
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    role: str | None = None,
    difficulty: str | None = None,
    college: str | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    ) -> DashboardTopListResponse:
    del current_user
    service = AnalyticsService(session)
    items = await service.get_role_segment_analytics(
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    )
    return DashboardTopListResponse(table_type="top_roles", items=_zero_fill_metric_nulls(items[:limit]))


@router.get(
    "/dashboard/top-colleges",
    response_model=DashboardTopListResponse,
    status_code=200,
    summary="Top colleges by interview volume",
    description="Reasoning: surfaces institutional adoption and performance concentration for program decisions."
    " Output: top-list rows keyed by college with aggregate metrics.",
)
async def get_dashboard_top_colleges(
    limit: int = fastapi.Query(default=5, ge=1, le=20),
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    role: str | None = None,
    difficulty: str | None = None,
    college: str | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    items = await service.get_college_segment_analytics(
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    )
    return DashboardTopListResponse(table_type="top_colleges", items=_zero_fill_metric_nulls(items[:limit]))


@router.get(
    "/dashboard/score-distribution",
    response_model=DistributionResponse,
    status_code=200,
    summary="Score distribution histogram",
    description="Reasoning: reveals score spread and calibration quality instead of relying only on averages."
    " Output: histogram buckets with range label and count.",
)
async def get_dashboard_score_distribution(
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    role: str | None = None,
    difficulty: str | None = None,
    college: str | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    scoring = await service.get_scoring_analytics(
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    )
    buckets = _extract_distribution_buckets(scoring.get("score_distribution", []))
    return DistributionResponse(chart_type="histogram", buckets=buckets)


@router.get(
    "/dashboard/recent-interviews",
    response_model=TablePageResponse,
    status_code=200,
    summary="Recent interviews table",
    description="Reasoning: gives near-real-time visibility into latest interview activity for support and QA."
    " Output: paginated table of recent interview rows.",
)
async def get_dashboard_recent_interviews(
    limit: int = fastapi.Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    stmt = (
        sqlalchemy.select(Interview, User.name, User.university, Report.overall_score)
        .join(User, User.id == Interview.user_id)
        .outerjoin(Report, Report.interview_id == Interview.id)
        .order_by(Interview.created_at.desc())
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).all())
    items = [
        {
            "interview_id": interview.id,
            "student_name": student_name,
            "college": university,
            "role": interview.track,
            "difficulty": interview.difficulty,
            "score": _metric_or_zero(overall_score, digits=2),
            "duration_seconds": _metric_or_zero(interview.duration_seconds),
            "date": interview.created_at,
            "status": interview.status,
        }
        for interview, student_name, university, overall_score in rows
    ]
    return TablePageResponse(table_type="recent_interviews", items=items, page=1, limit=limit, total=len(items))


@router.get(
    "/dashboard/recent-students",
    response_model=TablePageResponse,
    status_code=200,
    summary="Recently active students",
    description="Reasoning: helps identify currently engaged learners for outreach and follow-up actions."
    " Output: paginated table of recent student activity.",
)
async def get_dashboard_recent_students(
    limit: int = fastapi.Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    stmt = sqlalchemy.select(User).order_by(User.created_at.desc()).limit(limit)
    users = list((await session.execute(stmt)).scalars().all())
    items = [
        {
            "student_id": user.id,
            "name": user.name,
            "email": user.email,
            "college": user.university,
            "target_position": user.target_position,
            "created_at": user.created_at,
        }
        for user in users
    ]
    return TablePageResponse(table_type="recent_students", items=items, page=1, limit=limit, total=len(items))


@router.get("/dashboard/attention-required", response_model=TablePageResponse, status_code=200, summary="Students needing attention", description="Reasoning: prioritizes intervention candidates based on alert signals. Output: table rows of student/system attention items.")
async def get_dashboard_attention_required(
    limit: int = fastapi.Query(default=20, ge=1, le=100),
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    ) -> TablePageResponse:
    del current_user
    service = AnalyticsService(session)
    alerts = await service.get_alerts(start_date=start_date, end_date=end_date)
    items: list[dict[str, Any]] = []
    for alert in alerts.get("student_alerts", []):
        items.append({"entity_type": "student", "severity": "medium", **alert})
    for alert in alerts.get("system_alerts", []):
        items.append({"entity_type": "system", "severity": "high", **alert})
    return TablePageResponse(table_type="attention_required", items=items[:limit], page=1, limit=limit, total=len(items))


@router.get("/students/summary", response_model=StudentsSummaryResponse, status_code=200, summary="Student analytics summary", description="Reasoning: provides fast cohort-level learner health snapshot. Output: aggregate student KPI cards.")
async def get_students_summary(
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    ) -> StudentsSummaryResponse:
    del current_user
    service = AnalyticsService(session)
    system_metrics = await service.get_system_analytics()

    total_interviews = int((await session.execute(sqlalchemy.select(sqlalchemy.func.count(Interview.id)))).scalar() or 0)
    completed_interviews = int(
        (await session.execute(sqlalchemy.select(sqlalchemy.func.count(Interview.id)).where(Interview.status == "completed"))).scalar() or 0
    )
    overview = system_metrics.get("overview", {})
    kpis = [
        KpiCard(key="total_students", label="Total Students", value=int(overview.get("total_users", 0))),
        KpiCard(key="active_students_last_30_days", label="Active Students (Last 30 Days)", value=int(overview.get("active_users_30d", 0))),
        KpiCard(key="average_score", label="Average Score", value=overview.get("avg_score")),
        KpiCard(key="total_interviews", label="Total Interviews", value=total_interviews),
        KpiCard(key="overall_improvement_percent", label="Overall Improvement %", value=overview.get("improvement_percent"), unit="percent"),
        KpiCard(key="completion_rate", label="Completion Rate", value=_safe_percent(completed_interviews, total_interviews), unit="percent"),
    ]
    return StudentsSummaryResponse(kpis=kpis)


@router.get(
    "/students",
    response_model=TablePageResponse,
    status_code=200,
    summary="List students with analytics",
    description="Returns a paginated students table with score, improvement, interview count, and last-active fields."
    " Use q and college filters to narrow results.",
)
async def get_students_table(
    page: int = fastapi.Query(default=1, ge=1),
    limit: int = fastapi.Query(default=20, ge=1, le=100),
    q: str | None = None,
    college: str | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    users_stmt = sqlalchemy.select(User)
    if college:
        users_stmt = users_stmt.where(User.university == college)
    if q:
        pattern = f"%{q.strip()}%"
        users_stmt = users_stmt.where(
            sqlalchemy.or_(
                User.name.ilike(pattern),
                User.email.ilike(pattern),
                User.university.ilike(pattern),
            )
        )

    total_stmt = sqlalchemy.select(sqlalchemy.func.count()).select_from(users_stmt.subquery())
    total = int((await session.execute(total_stmt)).scalar() or 0)

    offset = (page - 1) * limit
    users_stmt = users_stmt.order_by(User.created_at.desc()).offset(offset).limit(limit)
    users = list((await session.execute(users_stmt)).scalars().all())
    if not users:
        return TablePageResponse(table_type="students", items=[], page=page, limit=limit, total=total)

    user_ids = [user.id for user in users]
    interviews_stmt = sqlalchemy.select(Interview).where(Interview.user_id.in_(user_ids)).order_by(Interview.created_at.asc())
    interviews = list((await session.execute(interviews_stmt)).scalars().all())

    interview_ids = [interview.id for interview in interviews]
    reports_map: dict[int, float | None] = {}
    if interview_ids:
        report_rows = list((await session.execute(sqlalchemy.select(Report).where(Report.interview_id.in_(interview_ids)))).scalars().all())
        summary_rows = list((await session.execute(sqlalchemy.select(SummaryReport).where(SummaryReport.interview_id.in_(interview_ids)))).scalars().all())
        
        report_dict = {r.interview_id: r for r in report_rows}
        summary_dict = {sr.interview_id: sr for sr in summary_rows}
        
        from src.services.analytics import _extract_overall_score
        reports_map = {
            int(interview_id): _extract_overall_score(report_dict.get(interview_id), summary_dict.get(interview_id))
            for interview_id in interview_ids
        }

    interviews_by_user: dict[int, list[Interview]] = defaultdict(list)
    for interview in interviews:
        interviews_by_user[interview.user_id].append(interview)

    items: list[dict[str, Any]] = []
    for user in users:
        user_interviews = interviews_by_user.get(user.id, [])
        scores = [reports_map.get(interview.id) for interview in user_interviews if reports_map.get(interview.id) is not None]
        avg_score = round(sum(scores) / len(scores), 2) if scores else 0

        latest_score = 0
        if user_interviews:
            latest_interview = sorted(user_interviews, key=lambda interview: interview.created_at or datetime.datetime.min)[-1]
            score_value = reports_map.get(latest_interview.id)
            latest_score = round(score_value, 2) if isinstance(score_value, (float, int)) else 0

        improvement_percent = 0
        if user_interviews and len(scores) >= 2:
            first_score = next((reports_map.get(interview.id) for interview in user_interviews if reports_map.get(interview.id) is not None), None)
            last_score = next((reports_map.get(interview.id) for interview in reversed(user_interviews) if reports_map.get(interview.id) is not None), None)
            if isinstance(first_score, (float, int)) and isinstance(last_score, (float, int)) and first_score > 0:
                improvement_percent = round(((last_score - first_score) / first_score) * 100.0, 2)

        last_active = max((interview.created_at for interview in user_interviews if interview.created_at is not None), default=None)
        items.append(
            {
                "student_id": user.id,
                "name": user.name,
                "college": user.university,
                "average_score": avg_score,
                "latest_score": latest_score,
                "improvement_percent": improvement_percent,
                "interviews_count": len(user_interviews),
                "last_active": last_active,
            }
        )

    return TablePageResponse(table_type="students", items=items, page=page, limit=limit, total=total)


@router.get("/students/search", response_model=TablePageResponse, status_code=200, summary="Search students by name", description="Reasoning: supports targeted lookup for mentoring and operations. Output: paginated student rows matching query text.")
async def search_students(
    q: str = fastapi.Query(min_length=1),
    page: int = fastapi.Query(default=1, ge=1),
    limit: int = fastapi.Query(default=20, ge=1, le=100),
    college: str | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    ) -> TablePageResponse:
    return await get_students_table(page=page, limit=limit, q=q, college=college, current_user=current_user, session=session)


@router.get("/students/filters/colleges", response_model=CollegesFilterResponse, status_code=200, summary="List available college filters", description="Reasoning: ensures UI uses backend-derived college filter options. Output: distinct college-name list.")
async def get_student_college_filters(
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    stmt = sqlalchemy.select(User.university).where(User.university.is_not(None)).distinct().order_by(User.university.asc())
    colleges = [row[0] for row in (await session.execute(stmt)).all() if row[0]]
    return CollegesFilterResponse(colleges=colleges)


@router.get(
    "/students/{student_id}/profile",
    response_model=StudentProfileResponse,
    status_code=200,
    summary="Student profile details",
    description="Returns core profile attributes for one student, including education and targeting fields.",
    responses={404: {"description": "Student does not exist."}},
)
async def get_student_profile(
    student_id: int,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    student = (await session.execute(sqlalchemy.select(User).where(User.id == student_id))).scalar_one_or_none()
    if student is None:
        raise fastapi.HTTPException(status_code=404, detail="Student not found")
    last_active = (
        await session.execute(
            sqlalchemy.select(sqlalchemy.func.max(Interview.created_at)).where(Interview.user_id == student_id)
        )
    ).scalar_one_or_none()
    return StudentProfileResponse(
        student_id=student.id,
        name=student.name,
        email=student.email,
        college=student.university,
        degree=student.degree,
        target_position=student.target_position,
        years_experience=student.years_experience,
        company=student.company,
        created_at=student.created_at,
        last_active=last_active,
    )


@router.get(
    "/students/{student_id}/summary",
    response_model=StudentSummaryResponse,
    status_code=200,
    summary="Student KPI summary",
    description="Reasoning: provides a concise progress snapshot for one student without loading full history."
    " Output: KPI card list for interview, score, and practice indicators.",
)
async def get_student_summary(
    student_id: int,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    metrics = await service.get_student_level_analytics(user_id=student_id, start_date=start_date, end_date=end_date)
    performance = metrics.get("performance", {})
    attempts = metrics.get("attempt_behavior", {})
    practice = metrics.get("practice_compliance", {})
    kpis = [
        KpiCard(key="total_interviews", label="Total Interviews", value=attempts.get("interviews_attempted", 0)),
        KpiCard(key="average_score", label="Average Score", value=_metric_or_zero(performance.get("average_last_3"), digits=2)),
        KpiCard(key="improvement_percent", label="Improvement %", value=_metric_or_zero(performance.get("improvement_rate"), digits=2), unit="percent"),
        KpiCard(key="last_active_date", label="Last Active Date", value=(performance.get("score_history", [])[-1].get("created_at") if performance.get("score_history") else None)),
        KpiCard(key="practice_completion_rate", label="Practice Completion Rate", value=_metric_or_zero(practice.get("completion_ratio"), digits=2), unit="ratio"),
        KpiCard(key="speech_score", label="Speech Score", value=_metric_or_zero((performance.get("score_history", [])[-1].get("speech_score") if performance.get("score_history") else None), digits=2)),
        KpiCard(key="knowledge_score", label="Knowledge Score", value=_metric_or_zero((performance.get("score_history", [])[-1].get("knowledge_score") if performance.get("score_history") else None), digits=2)),
    ]
    return StudentSummaryResponse(student_id=student_id, kpis=kpis)


@router.get("/students/{student_id}/score-history", response_model=StudentScoreHistoryResponse, status_code=200, summary="Student score history", description="Reasoning: shows longitudinal progression for one learner. Output: score trend time-series for the student.")
async def get_student_score_history(
    student_id: int,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    metrics = await service.get_student_level_analytics(user_id=student_id, start_date=start_date, end_date=end_date)
    points = metrics.get("performance", {}).get("score_history", [])
    return StudentScoreHistoryResponse(student_id=student_id, chart_type="line", points=points)


@router.get("/students/{student_id}/speech-vs-knowledge-history", response_model=StudentScoreHistoryResponse, status_code=200, summary="Speech vs knowledge trend", description="Reasoning: compares communication and domain progress over time. Output: time-series points for speech/knowledge dimensions.")
async def get_student_speech_vs_knowledge_history(
    student_id: int,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    metrics = await service.get_student_level_analytics(user_id=student_id, start_date=start_date, end_date=end_date)
    history = metrics.get("performance", {}).get("score_history", [])
    points = [
        {
            "interview_id": item.get("interview_id"),
            "created_at": item.get("created_at"),
            "speech_score": _metric_or_zero(item.get("speech_score"), digits=2),
            "knowledge_score": _metric_or_zero(item.get("knowledge_score"), digits=2),
        }
        for item in history
    ]
    return StudentScoreHistoryResponse(student_id=student_id, chart_type="line", points=points)


@router.get("/students/{student_id}/skill-averages", response_model=StudentSkillAveragesResponse, status_code=200, summary="Student skill averages", description="Reasoning: highlights stable strengths and weaknesses across subskills. Output: metric-value items for radar-like visualization.")
async def get_student_skill_averages(
    student_id: int,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    metrics = await service.get_student_level_analytics(user_id=student_id, start_date=start_date, end_date=end_date)
    speech_metrics = metrics.get("skill_breakdown", {}).get("speech", {})
    knowledge_metrics = metrics.get("skill_breakdown", {}).get("knowledge", {})

    items: list[dict[str, Any]] = []
    for metric_key, entries in {**speech_metrics, **knowledge_metrics}.items():
        numeric_values = [entry.get("value") for entry in entries if isinstance(entry.get("value"), (int, float))]
        avg_value = round(sum(numeric_values) / len(numeric_values), 2) if numeric_values else 0
        items.append({"metric": metric_key, "value": avg_value})

    weak_area_tags = metrics.get("weak_area_tags", [])
    for tag in weak_area_tags:
        items.append({"metric": tag, "value": 0})

    return StudentSkillAveragesResponse(student_id=student_id, chart_type="radar", items=items)


@router.get("/students/{student_id}/practice-completion", response_model=StudentPracticeCompletionResponse, status_code=200, summary="Student practice completion metrics", description="Reasoning: tracks completion of recommended practice and related impact. Output: practice KPI cards for the student.")
async def get_student_practice_completion(
    student_id: int,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    metrics = await service.get_student_level_analytics(user_id=student_id, start_date=start_date, end_date=end_date)
    practice = metrics.get("practice_compliance", {})
    kpis = [
        KpiCard(key="recommended_exercises", label="Recommended Exercises", value=_metric_or_zero(practice.get("recommended_exercises"))),
        KpiCard(key="completed_exercises", label="Completed Exercises", value=_metric_or_zero(practice.get("completed_exercises"))),
        KpiCard(key="completion_ratio", label="Completion Ratio", value=_metric_or_zero(practice.get("completion_ratio"), digits=2), unit="ratio"),
        KpiCard(
            key="improvement_after_practice",
            label="Improvement After Practice",
            value=_metric_or_zero((practice.get("improvement_after_practice") or {}).get("delta"), digits=2),
            unit="score",
        ),
    ]
    return StudentPracticeCompletionResponse(student_id=student_id, kpis=kpis)


@router.get("/students/{student_id}/interviews", response_model=TablePageResponse, status_code=200, summary="Student interview history", description="Reasoning: provides complete interview timeline for learner-level review. Output: paginated interview rows scoped to student.")
async def get_student_interviews(
    student_id: int,
    page: int = fastapi.Query(default=1, ge=1),
    limit: int = fastapi.Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    total_stmt = sqlalchemy.select(sqlalchemy.func.count(Interview.id)).where(Interview.user_id == student_id)
    total = int((await session.execute(total_stmt)).scalar() or 0)
    offset = (page - 1) * limit

    stmt = (
        sqlalchemy.select(Interview, Report.overall_score)
        .outerjoin(Report, Report.interview_id == Interview.id)
        .where(Interview.user_id == student_id)
        .order_by(Interview.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).all())
    items = [
        {
            "interview_id": interview.id,
            "role": interview.track,
            "difficulty": interview.difficulty,
            "status": interview.status,
            "score": _metric_or_zero(score, digits=2),
            "duration_seconds": _metric_or_zero(interview.duration_seconds),
            "created_at": interview.created_at,
        }
        for interview, score in rows
    ]
    return TablePageResponse(table_type="student_interviews", items=items, page=page, limit=limit, total=total)


@router.get(
    "/students/{student_id}/latest-feedback",
    response_model=StudentLatestFeedbackResponse,
    status_code=200,
    summary="Latest student feedback",
    description="Reasoning: exposes the most recent qualitative coaching note for rapid mentor review."
    " Output: latest feedback text with related attempt/interview metadata.",
)
async def get_student_latest_feedback(
    student_id: int,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    stmt = (
        sqlalchemy.select(QuestionAttempt)
        .join(Interview, Interview.id == QuestionAttempt.interview_id)
        .where(Interview.user_id == student_id, QuestionAttempt.feedback.is_not(None))
        .order_by(QuestionAttempt.created_at.desc())
        .limit(1)
    )
    latest = (await session.execute(stmt)).scalar_one_or_none()
    if latest is None:
        return StudentLatestFeedbackResponse(student_id=student_id)
    return StudentLatestFeedbackResponse(
        student_id=student_id,
        latest_feedback=latest.feedback,
        question_attempt_id=latest.id,
        interview_id=latest.interview_id,
        created_at=latest.created_at,
    )


@router.get(
    "/colleges/summary",
    response_model=CollegesSummaryResponse,
    status_code=200,
    summary="College analytics summary",
    description="Returns aggregate KPI cards at college level, including totals and top/bottom performance indicators.",
)
async def get_colleges_summary(
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    college_items = await service.get_college_segment_analytics()

    total_colleges = len(college_items)
    total_students = int((await session.execute(sqlalchemy.select(sqlalchemy.func.count(User.id)))).scalar() or 0)
    total_interviews = int((await session.execute(sqlalchemy.select(sqlalchemy.func.count(Interview.id)))).scalar() or 0)
    avg_scores = [item.get("avg_score") for item in college_items if isinstance(item.get("avg_score"), (int, float))]
    average_score = round(sum(avg_scores) / len(avg_scores), 2) if avg_scores else 0

    highest = max(college_items, key=lambda item: item.get("avg_score") if isinstance(item.get("avg_score"), (int, float)) else -1, default=None)
    lowest = min(
        [item for item in college_items if isinstance(item.get("avg_score"), (int, float))],
        key=lambda item: item.get("avg_score"),
        default=None,
    )

    kpis = [
        KpiCard(key="total_colleges", label="Total Colleges", value=total_colleges),
        KpiCard(key="total_students", label="Total Students", value=total_students),
        KpiCard(key="total_interviews", label="Total Interviews", value=total_interviews),
        KpiCard(key="average_score", label="Average Score", value=average_score),
        KpiCard(key="highest_performing_college", label="Highest Performing College", value=highest.get("college") if highest else None),
        KpiCard(key="lowest_performing_college", label="Lowest Performing College", value=lowest.get("college") if lowest else None),
    ]
    return CollegesSummaryResponse(kpis=kpis)


@router.get(
    "/colleges",
    response_model=TablePageResponse,
    status_code=200,
    summary="List colleges with metrics",
    description="Returns a paginated college metrics table with student counts, interview volume, average score,"
    " improvement, and activity values.",
)
async def get_colleges_table(
    page: int = fastapi.Query(default=1, ge=1),
    limit: int = fastapi.Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    all_items = await service.get_college_segment_analytics()
    total = len(all_items)
    start_index = (page - 1) * limit
    end_index = start_index + limit

    students_by_college_stmt = (
        sqlalchemy.select(User.university, sqlalchemy.func.count(User.id))
        .where(User.university.is_not(None))
        .group_by(User.university)
    )
    students_by_college = {name: count for name, count in (await session.execute(students_by_college_stmt)).all()}

    items = []
    for item in all_items[start_index:end_index]:
        college_name = item.get("college")
        items.append(
            {
                "college_name": college_name,
                "students_count": int(students_by_college.get(college_name, 0)),
                "interviews_count": item.get("interviews"),
                "avg_score": _metric_or_zero(item.get("avg_score"), digits=2),
                "improvement_percent": _metric_or_zero(item.get("improvement_rate"), digits=2),
                "active_users": item.get("usage_frequency"),
            }
        )
    return TablePageResponse(table_type="colleges", items=items, page=page, limit=limit, total=total)


@router.get(
    "/interviews/summary",
    response_model=InterviewsSummaryResponse,
    status_code=200,
    summary="Interview analytics summary",
    description="Returns interview-level KPI cards such as total interviews, average score, completion rate,"
    " average duration, and most popular role/difficulty.",
)
async def get_interviews_summary(
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    total_interviews = int((await session.execute(sqlalchemy.select(sqlalchemy.func.count(Interview.id)))).scalar() or 0)
    completed_interviews = int(
        (await session.execute(sqlalchemy.select(sqlalchemy.func.count(Interview.id)).where(Interview.status == "completed"))).scalar() or 0
    )
    average_duration = (await session.execute(sqlalchemy.select(sqlalchemy.func.avg(Interview.duration_seconds)))).scalar_one_or_none()
    popular_role = (
        await session.execute(
            sqlalchemy.select(Interview.track, sqlalchemy.func.count(Interview.id).label("interviews_count"))
            .group_by(Interview.track)
            .order_by(sqlalchemy.text("interviews_count DESC"))
            .limit(1)
        )
    ).first()
    popular_difficulty = (
        await session.execute(
            sqlalchemy.select(Interview.difficulty, sqlalchemy.func.count(Interview.id).label("interviews_count"))
            .group_by(Interview.difficulty)
            .order_by(sqlalchemy.text("interviews_count DESC"))
            .limit(1)
        )
    ).first()
    average_score = (await session.execute(sqlalchemy.select(sqlalchemy.func.avg(Report.overall_score)))).scalar_one_or_none()

    kpis = [
        KpiCard(key="total_interviews", label="Total Interviews", value=total_interviews),
        KpiCard(key="average_score", label="Average Score", value=_metric_or_zero(average_score, digits=2)),
        KpiCard(key="completion_rate", label="Completion Rate", value=_safe_percent(completed_interviews, total_interviews), unit="percent"),
        KpiCard(key="average_duration", label="Average Duration", value=int(_metric_or_zero(average_duration)), unit="seconds"),
        KpiCard(key="most_popular_role", label="Most Popular Role", value=popular_role[0] if popular_role else None),
        KpiCard(key="most_popular_difficulty", label="Most Popular Difficulty", value=popular_difficulty[0] if popular_difficulty else None),
    ]
    return InterviewsSummaryResponse(kpis=kpis)


@router.get(
    "/interviews",
    response_model=TablePageResponse,
    status_code=200,
    summary="List interviews with metrics",
    description="Returns a paginated interviews table with candidate, role, difficulty, score, duration, and date."
    " Supports role, difficulty, college, and date-range filters.",
)
async def get_interviews_table(
    page: int = fastapi.Query(default=1, ge=1),
    limit: int = fastapi.Query(default=20, ge=1, le=100),
    role: str | None = None,
    difficulty: str | None = None,
    college: str | None = None,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    base_stmt = _apply_interview_filters(
        sqlalchemy.select(Interview.id),
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    )
    total = int((await session.execute(sqlalchemy.select(sqlalchemy.func.count()).select_from(base_stmt.subquery()))).scalar() or 0)

    offset = (page - 1) * limit
    rows_stmt = _apply_interview_filters(
        sqlalchemy.select(Interview, User.name, User.university, Report.overall_score)
        .join(User, User.id == Interview.user_id)
        .outerjoin(Report, Report.interview_id == Interview.id),
        start_date=start_date,
        end_date=end_date,
        role=role,
        difficulty=difficulty,
        college=college,
    ).order_by(Interview.created_at.desc()).offset(offset).limit(limit)

    rows = list((await session.execute(rows_stmt)).all())
    items = [
        {
            "interview_id": interview.id,
            "student_name": student_name,
            "college": university,
            "role": interview.track,
            "difficulty": interview.difficulty,
            "score": _metric_or_zero(score, digits=2),
            "duration": _metric_or_zero(interview.duration_seconds),
            "date": interview.created_at,
        }
        for interview, student_name, university, score in rows
    ]
    return TablePageResponse(table_type="interviews", items=items, page=page, limit=limit, total=total)


@router.get(
    "/search",
    response_model=GlobalSearchResponse,
    status_code=200,
    summary="Global analytics search",
    description="Searches students, colleges, and interviews in one call and returns grouped result buckets.",
)
async def global_search(
    q: str = fastapi.Query(min_length=1),
    limit_per_bucket: int = fastapi.Query(default=5, ge=1, le=25),
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    pattern = f"%{q.strip()}%"

    users_stmt = (
        sqlalchemy.select(User.id, User.name, User.email, User.university)
        .where(sqlalchemy.or_(User.name.ilike(pattern), User.email.ilike(pattern), User.university.ilike(pattern)))
        .order_by(User.created_at.desc())
        .limit(limit_per_bucket)
    )
    users_rows = list((await session.execute(users_stmt)).all())

    colleges_stmt = (
        sqlalchemy.select(User.university, sqlalchemy.func.count(User.id).label("students_count"))
        .where(User.university.is_not(None), User.university.ilike(pattern))
        .group_by(User.university)
        .order_by(sqlalchemy.text("students_count DESC"))
        .limit(limit_per_bucket)
    )
    colleges_rows = list((await session.execute(colleges_stmt)).all())

    interviews_stmt = (
        sqlalchemy.select(Interview.id, Interview.track, Interview.difficulty, Interview.created_at, User.name)
        .join(User, User.id == Interview.user_id)
        .where(sqlalchemy.or_(Interview.track.ilike(pattern), Interview.difficulty.ilike(pattern), User.name.ilike(pattern)))
        .order_by(Interview.created_at.desc())
        .limit(limit_per_bucket)
    )
    interviews_rows = list((await session.execute(interviews_stmt)).all())

    return GlobalSearchResponse(
        students=[
            {
                "student_id": row.id,
                "name": row.name,
                "email": row.email,
                "college": row.university,
            }
            for row in users_rows
        ],
        colleges=[
            {
                "college_name": row.university,
                "students_count": int(row.students_count),
            }
            for row in colleges_rows
        ],
        interviews=[
            {
                "interview_id": row.id,
                "role": row.track,
                "difficulty": row.difficulty,
                "date": row.created_at,
                "student_name": row.name,
            }
            for row in interviews_rows
        ],
    )


@router.get(
    "/colleges/{college_name}/summary",
    response_model=CollegesSummaryResponse,
    status_code=200,
    summary="College detail summary",
    description="Reasoning: provides a one-college performance snapshot for institution-level accountability."
    " Output: KPI cards scoped to the selected college.",
)
async def get_college_detail_summary(
    college_name: str,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    college_items = await service.get_college_segment_analytics(college=college_name)
    summary = college_items[0] if college_items else {}

    students_count = int(
        (
            await session.execute(
                sqlalchemy.select(sqlalchemy.func.count(User.id)).where(User.university == college_name)
            )
        ).scalar()
        or 0
    )
    students_with_interviews = int(
        (
            await session.execute(
                sqlalchemy.select(sqlalchemy.func.count(sqlalchemy.distinct(Interview.user_id)))
                .join(User, User.id == Interview.user_id)
                .where(User.university == college_name)
            )
        ).scalar()
        or 0
    )
    completion_rate = summary.get("completion_rate")
    kpis = [
        KpiCard(key="total_students_enrolled", label="Total Students Enrolled", value=students_count),
        KpiCard(key="students_with_interviews", label="Students Who Have Given Interviews", value=students_with_interviews),
        KpiCard(key="average_score", label="Average Score", value=_metric_or_zero(summary.get("avg_score"), digits=2)),
        KpiCard(key="improvement_percent", label="Improvement %", value=_metric_or_zero(summary.get("improvement_rate"), digits=2), unit="percent"),
        KpiCard(key="active_users_last_30_days", label="Active Users (Last 30 Days)", value=summary.get("usage_frequency")),
        KpiCard(key="completion_rate", label="Completion Rate", value=completion_rate, unit="percent"),
    ]
    return CollegesSummaryResponse(kpis=kpis)


@router.get(
    "/colleges/{college_name}/student-growth",
    response_model=TimeSeriesResponse,
    status_code=200,
    summary="College student growth trend",
    description="Reasoning: tracks onboarding momentum at the college level over time."
    " Output: cumulative student growth points by date.",
)
async def get_college_student_growth(
    college_name: str,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    stmt = (
        sqlalchemy.select(sqlalchemy.func.date(User.created_at), sqlalchemy.func.count(User.id))
        .where(User.university == college_name)
        .group_by(sqlalchemy.func.date(User.created_at))
        .order_by(sqlalchemy.func.date(User.created_at).asc())
    )
    rows = list((await session.execute(stmt)).all())
    cumulative = 0
    points: list[TimeSeriesPoint] = []
    for day, count in rows:
        if day is None:
            continue
        cumulative += int(count)
        points.append(TimeSeriesPoint(date=day, value=cumulative))
    return TimeSeriesResponse(chart_type="line", points=points)


@router.get(
    "/colleges/{college_name}/score-trend",
    response_model=TimeSeriesResponse,
    status_code=200,
    summary="College score trend",
    description="Reasoning: highlights learning outcomes trajectory for a specific college cohort."
    " Output: date-wise average score trend points.",
)
async def get_college_score_trend(
    college_name: str,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    stmt = (
        sqlalchemy.select(sqlalchemy.func.date(Interview.created_at), sqlalchemy.func.avg(Report.overall_score))
        .join(User, User.id == Interview.user_id)
        .outerjoin(Report, Report.interview_id == Interview.id)
        .where(User.university == college_name)
        .group_by(sqlalchemy.func.date(Interview.created_at))
        .order_by(sqlalchemy.func.date(Interview.created_at).asc())
    )
    rows = list((await session.execute(stmt)).all())
    points = [
        TimeSeriesPoint(date=day, value=round(float(avg_score), 2))
        for day, avg_score in rows
        if day is not None and avg_score is not None
    ]
    return TimeSeriesResponse(chart_type="line", points=points)


@router.get(
    "/colleges/{college_name}/practice-metrics",
    response_model=DashboardTopListResponse,
    status_code=200,
    summary="College practice metrics",
    description="Reasoning: indicates whether a college needs practice-focused interventions."
    " Output: college practice-alert metrics row.",
)
async def get_college_practice_metrics(
    college_name: str,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    alerts = await service.get_alerts()
    college_alerts = [a for a in alerts.get("system_alerts", []) if a.get("college") == college_name]
    items = [
        {
            "college": college_name,
            "practice_alerts_count": len(college_alerts),
            "attention_required": bool(college_alerts),
        }
    ]
    return DashboardTopListResponse(table_type="college_practice_metrics", items=items)


@router.get("/colleges/{college_name}/weak-skills", response_model=HeatmapResponse, status_code=200, summary="College weak skills heatmap", description="Reasoning: reveals recurring role/weakness patterns inside one college. Output: heatmap cells with role, weakness tag, and frequency.")
async def get_college_weak_skills(
    college_name: str,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    stmt = (
        sqlalchemy.select(QuestionAttempt.analysis_json, Interview.track)
        .join(Interview, Interview.id == QuestionAttempt.interview_id)
        .join(User, User.id == Interview.user_id)
        .where(User.university == college_name)
    )
    rows = list((await session.execute(stmt)).all())
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for analysis_json, role in rows:
        analysis = analysis_json or {}
        communication = analysis.get("communication") if isinstance(analysis, dict) else {}
        domain = analysis.get("domain") if isinstance(analysis, dict) else {}
        weaknesses = []
        if isinstance(communication, dict):
            weaknesses.extend(communication.get("improvements") or [])
            weaknesses.extend(communication.get("recommendations") or [])
        if isinstance(domain, dict):
            weaknesses.extend(domain.get("improvements") or [])
        for weakness in weaknesses:
            if isinstance(weakness, str) and weakness.strip():
                counts[(role or "unknown", weakness.strip().lower())] += 1
    items = [HeatmapCell(x=role_name, y=tag, value=count) for (role_name, tag), count in counts.items()]
    return HeatmapResponse(chart_type="heatmap", items=items)


@router.get("/colleges/{college_name}/students", response_model=TablePageResponse, status_code=200, summary="Students in a college", description="Reasoning: enables institution-specific learner drill-down from college views. Output: paginated student analytics rows for selected college.")
async def get_college_students(
    college_name: str,
    page: int = fastapi.Query(default=1, ge=1),
    limit: int = fastapi.Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    return await get_students_table(page=page, limit=limit, q=None, college=college_name, current_user=current_user, session=session)


@router.get(
    "/rankings/summary",
    response_model=DashboardOverviewResponse,
    status_code=200,
    summary="Rankings overview KPIs",
    description="Returns ranking KPI cards derived from top performers, struggling students, and most-improved cohorts.",
)
async def get_rankings_summary(
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    top_rows = await get_top_performers(page=1, limit=10, current_user=current_user, session=session)
    struggling_rows = await get_struggling_students(page=1, limit=10, current_user=current_user, session=session)
    improved_rows = await get_most_improved_students(page=1, limit=10, current_user=current_user, session=session)

    top_scores = [row.get("average_score") for row in top_rows.items]
    struggling_scores = [row.get("average_score") for row in struggling_rows.items]
    improved_values = [row.get("improvement_percent") for row in improved_rows.items]
    kpis = [
        KpiCard(key="top_performers_count", label="Number of Top Performers", value=top_rows.total),
        KpiCard(key="struggling_students_count", label="Number of Struggling Students", value=struggling_rows.total),
        KpiCard(key="most_improved_count", label="Number of Most Improved Students", value=improved_rows.total),
        KpiCard(key="top_performers_avg", label="Average Score of Top Performers", value=_safe_avg(top_scores)),
        KpiCard(key="struggling_avg", label="Average Score of Struggling Students", value=_safe_avg(struggling_scores)),
        KpiCard(key="improved_avg", label="Average Improvement of Most Improved Students", value=_safe_avg(improved_values)),
    ]
    return DashboardOverviewResponse(kpis=kpis)


@router.get(
    "/rankings/top-performers",
    response_model=TablePageResponse,
    status_code=200,
    summary="Top performing students",
    description="Returns a paginated leaderboard sorted by highest average score.",
)
async def get_top_performers(
    page: int = fastapi.Query(default=1, ge=1),
    limit: int = fastapi.Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    table = await get_students_table(page=1, limit=1000, q=None, college=None, current_user=current_user, session=session)
    ranked = sorted(table.items, key=lambda row: row.get("average_score") if isinstance(row.get("average_score"), (int, float)) else -1, reverse=True)
    total = len([row for row in ranked if isinstance(row.get("average_score"), (int, float))])
    start = (page - 1) * limit
    end = start + limit
    return TablePageResponse(table_type="top_performers", items=ranked[start:end], page=page, limit=limit, total=total)


@router.get(
    "/rankings/struggling",
    response_model=TablePageResponse,
    status_code=200,
    summary="Struggling students",
    description="Returns a paginated list sorted by lowest average score.",
)
async def get_struggling_students(
    page: int = fastapi.Query(default=1, ge=1),
    limit: int = fastapi.Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    table = await get_students_table(page=1, limit=1000, q=None, college=None, current_user=current_user, session=session)
    ranked = sorted(table.items, key=lambda row: row.get("average_score") if isinstance(row.get("average_score"), (int, float)) else 10**9)
    total = len([row for row in ranked if isinstance(row.get("average_score"), (int, float))])
    start = (page - 1) * limit
    end = start + limit
    return TablePageResponse(table_type="struggling_students", items=ranked[start:end], page=page, limit=limit, total=total)


@router.get(
    "/rankings/most-improved",
    response_model=TablePageResponse,
    status_code=200,
    summary="Most improved students",
    description="Returns a paginated list sorted by highest improvement percentage.",
)
async def get_most_improved_students(
    page: int = fastapi.Query(default=1, ge=1),
    limit: int = fastapi.Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    table = await get_students_table(page=1, limit=1000, q=None, college=None, current_user=current_user, session=session)
    ranked = sorted(table.items, key=lambda row: row.get("improvement_percent") if isinstance(row.get("improvement_percent"), (int, float)) else -10**9, reverse=True)
    total = len([row for row in ranked if isinstance(row.get("improvement_percent"), (int, float))])
    start = (page - 1) * limit
    end = start + limit
    return TablePageResponse(table_type="most_improved", items=ranked[start:end], page=page, limit=limit, total=total)


@router.get(
    "/interviews/{interview_id}/summary",
    response_model=DashboardOverviewResponse,
    status_code=200,
    summary="Interview detail summary",
    description="Returns KPI cards for a single interview, including overall/speech/knowledge scores and completion state.",
    responses={404: {"description": "Interview not found."}},
)
async def get_interview_detail_summary(
    interview_id: int,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    metrics = await service.get_interview_level_analytics(interview_id=interview_id)
    if metrics is None:
        raise fastapi.HTTPException(status_code=404, detail="Interview not found")
    question_level = metrics.get("question_level", [])
    kpis = [
        KpiCard(key="overall_score", label="Overall Score", value=_metric_or_zero(metrics.get("total_score"), digits=2)),
        KpiCard(key="duration", label="Duration", value=_metric_or_zero(metrics.get("duration_seconds")), unit="seconds"),
        KpiCard(key="difficulty_level", label="Difficulty Level", value=metrics.get("difficulty")),
        KpiCard(key="questions_count", label="Number of Questions", value=len(question_level)),
        KpiCard(key="speech_score", label="Speech Score", value=_metric_or_zero(metrics.get("speech_score"), digits=2)),
        KpiCard(key="knowledge_score", label="Knowledge Score", value=_metric_or_zero(metrics.get("knowledge_score"), digits=2)),
        KpiCard(key="completion_status", label="Completion Status", value=metrics.get("status")),
    ]
    return DashboardOverviewResponse(kpis=kpis)


@router.get(
    "/interviews/{interview_id}/question-scores",
    response_model=TablePageResponse,
    status_code=200,
    summary="Interview question score table",
    description="Returns per-question analytics rows for one interview.",
    responses={404: {"description": "Interview not found."}},
)
async def get_interview_question_scores(
    interview_id: int,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    metrics = await service.get_interview_level_analytics(interview_id=interview_id)
    if metrics is None:
        raise fastapi.HTTPException(status_code=404, detail="Interview not found")
    items = metrics.get("question_level", [])
    return TablePageResponse(table_type="interview_question_scores", items=items, page=1, limit=len(items) or 1, total=len(items))


@router.get(
    "/interviews/{interview_id}/speech-metrics-timeline",
    response_model=TimeSeriesResponse,
    status_code=200,
    summary="Interview speech metrics timeline",
    description="Reasoning: shows how speech quality evolves across attempts/questions in an interview."
    " Output: time-series points for speech-energy related metric values.",
)
async def get_interview_speech_metrics_timeline(
    interview_id: int,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    stmt = (
        sqlalchemy.select(QuestionAttempt.created_at, QuestionAttempt.analysis_json)
        .where(QuestionAttempt.interview_id == interview_id)
        .order_by(QuestionAttempt.created_at.asc())
    )
    rows = list((await session.execute(stmt)).all())
    points: list[TimeSeriesPoint] = []
    for created_at, analysis_json in rows:
        analysis = analysis_json or {}
        communication = analysis.get("communication") if isinstance(analysis, dict) else {}
        energy = None
        if isinstance(communication, dict):
            raw_energy = communication.get("energy") or communication.get("energy_score")
            if isinstance(raw_energy, (int, float)):
                energy = float(raw_energy)
        point_date = _to_date(created_at)
        if point_date is not None and energy is not None:
            points.append(TimeSeriesPoint(date=point_date, value=round(energy, 2)))
    return TimeSeriesResponse(chart_type="line", points=points)


@router.get(
    "/interviews/{interview_id}/question-type-breakdown",
    response_model=DistributionResponse,
    status_code=200,
    summary="Interview question type breakdown",
    description="Reasoning: helps validate question-mix balance within an interview session."
    " Output: distribution buckets of question categories.",
)
async def get_interview_question_type_breakdown(
    interview_id: int,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    stmt = (
        sqlalchemy.select(InterviewQuestion.category, sqlalchemy.func.count(InterviewQuestion.id))
        .where(InterviewQuestion.interview_id == interview_id)
        .group_by(InterviewQuestion.category)
    )
    rows = list((await session.execute(stmt)).all())
    buckets = [DistributionBucket(label=(category or "unknown"), count=int(count)) for category, count in rows]
    return DistributionResponse(chart_type="pie", buckets=buckets)


@router.get("/roles/summary", response_model=DashboardOverviewResponse, status_code=200, summary="Role analytics summary", description="Reasoning: summarizes role-wise performance distribution for planning and benchmarking. Output: role-level KPI cards.")
async def get_roles_summary(
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    items = await service.get_role_segment_analytics()
    total_roles = len(items)
    all_interviews = sum(int(item.get("interviews", 0)) for item in items)
    completion_rates = [100.0 - float(item.get("drop_off_rate", 0.0)) for item in items]
    most_popular = max(items, key=lambda item: int(item.get("interviews", 0)), default=None)
    highest_avg = max(items, key=lambda item: item.get("avg_score") if isinstance(item.get("avg_score"), (int, float)) else -1, default=None)
    lowest_avg = min([item for item in items if isinstance(item.get("avg_score"), (int, float))], key=lambda item: item.get("avg_score"), default=None)
    kpis = [
        KpiCard(key="total_roles", label="Total Roles", value=total_roles),
        KpiCard(key="most_popular_role", label="Most Popular Role", value=(most_popular or {}).get("role")),
        KpiCard(key="highest_avg_role", label="Highest Average Score Role", value=(highest_avg or {}).get("role")),
        KpiCard(key="lowest_avg_role", label="Lowest Average Score Role", value=(lowest_avg or {}).get("role")),
        KpiCard(key="total_interviews_across_roles", label="Total Interviews Across Roles", value=all_interviews),
        KpiCard(key="overall_completion_rate", label="Overall Completion Rate", value=_safe_avg(completion_rates), unit="percent"),
    ]
    return DashboardOverviewResponse(kpis=kpis)


@router.get(
    "/roles/performance",
    response_model=TablePageResponse,
    status_code=200,
    summary="Role performance table",
    description="Returns role-level performance rows including interviews, average score, drop-off, and weak-skill hints.",
)
async def get_roles_performance(
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    items = await service.get_role_segment_analytics(start_date=start_date, end_date=end_date)
    normalized_items = _zero_fill_metric_nulls(items)
    return TablePageResponse(table_type="role_performance", items=normalized_items, page=1, limit=len(items) or 1, total=len(items))


@router.get("/roles/weak-skills", response_model=HeatmapResponse, status_code=200, summary="Role weak skills heatmap", description="Reasoning: surfaces repeated weakness themes by role for remediation design. Output: role-vs-weakness heatmap cells.")
async def get_roles_weak_skills(
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    items = await service.get_role_segment_analytics(start_date=start_date, end_date=end_date)
    cells: list[HeatmapCell] = []
    for role_item in items:
        role_name = role_item.get("role") or "unknown"
        for weakness in role_item.get("common_weaknesses", []):
            cells.append(HeatmapCell(x=str(role_name), y=str(weakness), value=1))
    return HeatmapResponse(chart_type="heatmap", items=cells)


@router.get("/roles/{role_id}", response_model=DashboardTopListResponse, status_code=200, summary="Role detail metrics", description="Reasoning: provides focused diagnostics for a specific role. Output: role-specific metrics list.")
async def get_role_detail(
    role_id: str,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    items = await service.get_role_segment_analytics(role=role_id)
    return DashboardTopListResponse(table_type="role_detail", items=_zero_fill_metric_nulls(items))


@router.get("/difficulty/metrics", response_model=TablePageResponse, status_code=200, summary="Difficulty level metrics", description="Reasoning: compares outcomes across difficulty levels to calibrate question strategy. Output: difficulty-wise metrics table.")
async def get_difficulty_metrics(
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    items = await service.get_difficulty_segment_analytics(start_date=start_date, end_date=end_date)
    items = _sort_difficulty_items(items)
    normalized_items = _zero_fill_metric_nulls(items)
    return TablePageResponse(table_type="difficulty_metrics", items=normalized_items, page=1, limit=len(items) or 1, total=len(items))


@router.get(
    "/questions/analytics",
    response_model=TablePageResponse,
    status_code=200,
    summary="Question level analytics",
    description="Returns a paginated question analytics table with attempts count and average score by question.",
)
async def get_questions_analytics(
    page: int = fastapi.Query(default=1, ge=1),
    limit: int = fastapi.Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    stmt = (
        sqlalchemy.select(
            InterviewQuestion.id,
            InterviewQuestion.text,
            InterviewQuestion.category,
            sqlalchemy.func.count(QuestionAttempt.id).label("attempts"),
            sqlalchemy.func.avg(Report.overall_score).label("avg_score"),
        )
        .outerjoin(QuestionAttempt, QuestionAttempt.question_id == InterviewQuestion.id)
        .outerjoin(Report, Report.interview_id == InterviewQuestion.interview_id)
        .group_by(InterviewQuestion.id)
        .order_by(InterviewQuestion.id.asc())
    )
    all_rows = list((await session.execute(stmt)).all())
    total = len(all_rows)
    start = (page - 1) * limit
    end = start + limit
    rows = all_rows[start:end]
    items = [
        {
            "question_id": row.id,
            "question_text": row.text,
            "question_type": row.category,
            "attempts": int(row.attempts or 0),
            "average_score": _metric_or_zero(row.avg_score, digits=2),
        }
        for row in rows
    ]
    return TablePageResponse(table_type="question_analytics", items=items, page=page, limit=limit, total=total)


@router.get(
    "/dropoffs/funnel",
    response_model=FunnelResponse,
    status_code=200,
    summary="Drop-off funnel",
    description="Returns ordered funnel stages with stage counts and conversion rates between consecutive stages.",
)
async def get_dropoff_funnel(
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    system = await service.get_system_analytics(start_date=start_date, end_date=end_date)
    funnel = system.get("funnel", {})
    sequence = ["sign_up", "select_role", "start_interview", "complete_interview", "view_report", "do_practice"]
    previous_count: int | None = None
    stages: list[FunnelStage] = []
    for stage in sequence:
        count = int(funnel.get(stage, 0))
        rate = _safe_percent(count, previous_count) if previous_count is not None and previous_count > 0 else 0
        stages.append(FunnelStage(stage=stage, count=count, rate=rate))
        previous_count = count
    return FunnelResponse(chart_type="funnel", stages=stages)


@router.get(
    "/insights/predictive-alerts",
    response_model=TablePageResponse,
    status_code=200,
    summary="Predictive alerts",
    description="Returns student and system risk alerts with prediction labels and confidence scores.",
)
async def get_predictive_alerts(
    page: int = fastapi.Query(default=1, ge=1),
    limit: int = fastapi.Query(default=20, ge=1, le=100),
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    alerts = await service.get_alerts(start_date=start_date, end_date=end_date)
    predicted_items: list[dict[str, Any]] = []
    for alert in alerts.get("student_alerts", []):
        predicted_items.append(
            {
                "entity_type": "student",
                "prediction": "high_risk_of_dropoff",
                "reason": alert.get("message"),
                "confidence": 0.7,
                **alert,
            }
        )
    for alert in alerts.get("system_alerts", []):
        predicted_items.append(
            {
                "entity_type": "system",
                "prediction": "cohort_performance_decline",
                "reason": alert.get("message"),
                "confidence": 0.75,
                **alert,
            }
        )
    total = len(predicted_items)
    start = (page - 1) * limit
    end = start + limit
    return TablePageResponse(table_type="predictive_alerts", items=predicted_items[start:end], page=page, limit=limit, total=total)


@router.get(
    "/insights/benchmarking",
    response_model=TablePageResponse,
    status_code=200,
    summary="Benchmarking insights",
    description="Returns role-level comparisons against platform average, including score deltas.",
)
async def get_benchmarking(
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    service = AnalyticsService(session)
    role_items = await service.get_role_segment_analytics()
    overall_avg = _safe_avg([item.get("avg_score") for item in role_items])
    items = []
    for item in role_items:
        role_avg = item.get("avg_score")
        delta = None
        if isinstance(role_avg, (int, float)) and isinstance(overall_avg, (int, float)):
            delta = round(float(role_avg) - float(overall_avg), 2)
        items.append(
            {
                "dimension": "role",
                "name": item.get("role"),
                "avg_score": role_avg,
                "platform_avg": overall_avg,
                "delta": delta,
            }
        )
    normalized_items = _zero_fill_metric_nulls(items)
    return TablePageResponse(table_type="benchmarking", items=normalized_items, page=1, limit=len(items) or 1, total=len(items))


@router.get(
    "/insights/forecasting",
    response_model=ForecastResponse,
    status_code=200,
    summary="Forecasting trend",
    description="Returns short-term forecast points for upcoming interview volume with lower/upper bounds.",
)
async def get_forecasting(
    days_ahead: int = fastapi.Query(default=7, ge=1, le=30),
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
):
    del current_user
    stmt = (
        sqlalchemy.select(sqlalchemy.func.date(Interview.created_at), sqlalchemy.func.count(Interview.id))
        .group_by(sqlalchemy.func.date(Interview.created_at))
        .order_by(sqlalchemy.func.date(Interview.created_at).asc())
    )
    rows = list((await session.execute(stmt)).all())
    if not rows:
        return ForecastResponse(chart_type="line", points=[])

    historical = [(row[0], int(row[1])) for row in rows if row[0] is not None]
    last_date = historical[-1][0]
    last_values = [count for _, count in historical[-7:]]
    baseline = _safe_avg(last_values) or 0.0
    points: list[ForecastPoint] = []
    for day_index in range(1, days_ahead + 1):
        date_value = last_date + datetime.timedelta(days=day_index)
        predicted = round(float(baseline), 2)
        points.append(
            ForecastPoint(
                date=date_value,
                predicted_value=predicted,
                lower_bound=max(0.0, round(predicted * 0.85, 2)),
                upper_bound=round(predicted * 1.15, 2),
            )
        )
    return ForecastResponse(chart_type="line", points=points)
