from __future__ import annotations

import logging
import fastapi
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.repository import get_repository
from src.api.dependencies.session import get_async_session
from src.models.db.user import User
from src.models.schemas.summary_report import SummaryReportRequest, SummaryReportResponse, SummaryReportsListResponse, SummaryReportListItem
from src.repository.crud.interview import InterviewCRUDRepository
from src.repository.crud.question import QuestionAttemptCRUDRepository
from src.repository.crud.summary_report import SummaryReportCRUDRepository
from src.services.summary_report_v2 import SummaryReportServiceV2
from src.services.analytics_events import track_analytics_event


router = fastapi.APIRouter(tags=["report"])


@router.post(
    "/summary-report",
    response_model=SummaryReportResponse,
    response_model_exclude_none=True,
    status_code=200,
    summary="Generate a summary report (independent of final report)",
    description=(
        "Aggregates per-question analyses into a UI-friendly summary layout. "
        "Does not persist to DB by default and is independent from /final-report."
    ),
)
async def generate_summary_report(
    payload: SummaryReportRequest,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    interview_repo: InterviewCRUDRepository = Depends(get_repository(InterviewCRUDRepository)),
    qa_repo: QuestionAttemptCRUDRepository = Depends(get_repository(QuestionAttemptCRUDRepository)),
    sr_repo: SummaryReportCRUDRepository = Depends(get_repository(SummaryReportCRUDRepository)),
):
    logger = logging.getLogger(__name__)
    logger.info("/summary-report called for interview_id=%s by user_id=%s", payload.interview_id, current_user.id)

    # Verify interview belongs to current user
    interview = await interview_repo.get_by_id_and_user(payload.interview_id, current_user.id)
    if not interview:
        raise fastapi.HTTPException(status_code=404, detail="Interview not found or access denied")

    # Fetch all question attempts for interview
    attempts = await qa_repo.list_by_interview(interview_id=interview.id)
    logger.debug("/summary-report assembling %d question attempts for interview_id=%s", len(attempts), interview.id)

    # Check if resume was used for any questions in this interview
    from src.repository.crud.interview_question import InterviewQuestionCRUDRepository
    question_repo = InterviewQuestionCRUDRepository(session)
    questions = await question_repo.list_by_interview(interview_id=interview.id)
    resume_used = any(q.resume_used for q in questions) if questions else None

    # Get candidate name from user if available
    candidate_name = getattr(current_user, "name", None)

    service = SummaryReportServiceV2(session)
    result = await service.generate_for_interview(
        interview.id, attempts, interview.track, resume_used, candidate_name
    )

    # Persist summary report (idempotent per interview)
    try:
        await sr_repo.upsert(interview_id=interview.id, report_json=result)
        await session.commit()
        logger.info("/summary-report persisted successfully for interview_id=%s", interview.id)
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        logger.exception("/summary-report failed to persist for interview_id=%s: %s", interview.id, exc)

    return SummaryReportResponse(**result)


@router.get(
    "/summary-report/{interview_id}",
    response_model=SummaryReportResponse,
    response_model_exclude_none=True,
    status_code=200,
    summary="Fetch a previously saved summary report",
    description="Retrieves a persisted summary report for the interview if present.",
)
async def get_summary_report(
    interview_id: int,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    interview_repo: InterviewCRUDRepository = Depends(get_repository(InterviewCRUDRepository)),
    sr_repo: SummaryReportCRUDRepository = Depends(get_repository(SummaryReportCRUDRepository)),
):
    logger = logging.getLogger(__name__)
    logger.info("GET /summary-report/%s by user_id=%s", interview_id, current_user.id)

    interview = await interview_repo.get_by_id_and_user(interview_id, current_user.id)
    if not interview:
        raise fastapi.HTTPException(status_code=404, detail="Interview not found or access denied")

    record = await sr_repo.get_by_interview_id(interview_id)
    if not record or not record.report_json:
        raise fastapi.HTTPException(status_code=404, detail="Summary report not found for this interview")

    await track_analytics_event(
        session,
        event_type="report_viewed",
        user_id=current_user.id,
        interview_id=interview.id,
        event_data={"report_type": "summary", "source": "summary_report.get_summary_report"},
    )

    return SummaryReportResponse(**record.report_json)


@router.get(
    "/summary-reports",
    response_model=SummaryReportsListResponse,
    response_model_exclude_none=True,
    status_code=200,
    summary="Get user's last x summary reports",
    description="Retrieves the user's most recent summary reports with interview IDs and tracks.",
)
async def get_summary_reports(
    limit: int = fastapi.Query(default=10, ge=1, le=50, description="Maximum number of reports to return"),
    current_user: User = Depends(get_current_user),
    sr_repo: SummaryReportCRUDRepository = Depends(get_repository(SummaryReportCRUDRepository)),
):
    logger = logging.getLogger(__name__)
    logger.info("GET /summary-reports?limit=%d by user_id=%s", limit, current_user.id)

    # Get the last x summary reports for the user
    reports_data = await sr_repo.get_last_x_for_user(user_id=current_user.id, limit=limit)
    
    # Convert to response format
    items = []
    for summary_report, interview in reports_data:
        # Extract overall score from the report JSON if available
        overall_score = None
        if summary_report.report_json:
            # Try new format first
            if "scoreSummary" in summary_report.report_json:
                score_summary = summary_report.report_json["scoreSummary"]
                kc_pct = score_summary.get("knowledgeCompetence", {}).get("percentage")
                ss_pct = score_summary.get("speechAndStructure", {}).get("percentage")
                
                # Compute overall score as average of available scores
                scores = [s for s in [kc_pct, ss_pct] if s is not None]
                if scores:
                    overall_score = sum(scores) / len(scores)
            # Fall back to old format
            elif "metrics" in summary_report.report_json:
                metrics_json = summary_report.report_json["metrics"]
                kc_pct = None
                ss_pct = None
                
                # For knowledge competence, check if we have valid data
                if "knowledgeCompetence" in metrics_json:
                    kc = metrics_json["knowledgeCompetence"]
                    kc_val = kc.get("averagePct")
                    breakdown = kc.get("breakdown", {}) or {}
                    has_breakdown_data = any(
                        val is not None and val > 0
                        for key in ["accuracy", "depth", "coverage", "relevance"]
                        if (val := breakdown.get(key)) is not None
                    )
                    if kc_val is not None and (kc_val > 0 or has_breakdown_data):
                        kc_pct = kc_val
                        
                if "speechStructure" in metrics_json and "averagePct" in metrics_json["speechStructure"]:
                    ss_pct = metrics_json["speechStructure"]["averagePct"]
                
                # Compute overall score as average of available scores
                scores = [s for s in [kc_pct, ss_pct] if s is not None]
                if scores:
                    overall_score = sum(scores) / len(scores)
        
        # Create the complete summary report response
        report_data = SummaryReportResponse(**summary_report.report_json) if summary_report.report_json else None
        
        if report_data:
            item = SummaryReportListItem(
                interview_id=interview.id,
                track=interview.track,
                difficulty=interview.difficulty,
                created_at=summary_report.created_at.isoformat(),
                overall_score=overall_score,
                report=report_data
            )
            items.append(item)
    
    return SummaryReportsListResponse(
        items=items,
        total_count=len(items),
        limit=limit
    )
