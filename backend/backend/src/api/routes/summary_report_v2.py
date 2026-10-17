"""V2 Summary Report routes with Lite response format."""

from __future__ import annotations

import logging
import time

import fastapi
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.repository import get_repository
from src.api.dependencies.session import get_async_session
from src.models.db.user import User
from src.models.schemas.summary_report import SummaryReportRequest
from src.models.schemas.summary_report_v2 import SummaryReportResponseLite
from src.repository.crud.interview import InterviewCRUDRepository
from src.repository.crud.question import QuestionAttemptCRUDRepository
from src.repository.crud.summary_report import SummaryReportCRUDRepository
from src.services.analysis import analysis_service
from src.services.analytics_events import track_analytics_event
from src.services.summary_report_v2 import SummaryReportServiceV2
from src.services.practice_feature_controls import require_practice_feature

logger = logging.getLogger(__name__)
router = fastapi.APIRouter(prefix="/v2", tags=["summary-report-v2"])


@router.post(
    "/summary-report",
    response_model=SummaryReportResponseLite,
    response_model_exclude_none=True,
    status_code=200,
    summary="Generate a V2 summary report (Lite version)",
    description=(
        "Aggregates per-question analyses into a UI-friendly summary layout with simplified feedback. "
        "Includes recommended practice, speech fluency rating with emoji, next steps, and final tip."
    ),
)
async def generate_summary_report_v2(
    payload: SummaryReportRequest,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    interview_repo: InterviewCRUDRepository = Depends(
        get_repository(InterviewCRUDRepository)
    ),
    qa_repo: QuestionAttemptCRUDRepository = Depends(
        get_repository(QuestionAttemptCRUDRepository)
    ),
    sr_repo: SummaryReportCRUDRepository = Depends(
        get_repository(SummaryReportCRUDRepository)
    ),
):
    await require_practice_feature(session, "report_generation")
    # logger = logging.getLogger(__name__)
    # logger.info("POST /v2/summary-report called for interview_id=%s by user_id=%s", payload.interview_id, current_user.id)
    request_start = time.perf_counter()

    logger.info(
        "Summary Report V2 generation started | Interview=%s | User=%s",
        payload.interview_id,
        current_user.id,
    )

    # Verify interview belongs to current user
    interview = await interview_repo.get_by_id_and_user(
        payload.interview_id, current_user.id
    )
    logger.info(
        "Interview verification completed | Interview=%s | Found=%s",
        payload.interview_id,
        interview is not None,
    )
    if not interview:
        raise fastapi.HTTPException(
            status_code=404, detail="Interview not found or access denied"
        )

    existing = await sr_repo.get_by_interview_id(interview.id)
    if (
        existing
        and isinstance(existing.report_json, dict)
        and existing.report_json.get("reportVersion") == "practice-report-v1"
        and existing.report_json.get("reportStatus") == "ready"
    ):
        logger.info(
            "Returning existing versioned Practice report | Interview=%s | Status=%s",
            interview.id,
            existing.report_json.get("reportStatus"),
        )
        return SummaryReportResponseLite(**existing.report_json)
    attempt_start = time.perf_counter()
    # Fetch all question attempts for interview
    attempts = await qa_repo.list_by_interview(interview_id=interview.id)
    logger.info(
        "Loaded question attempts | Interview=%s | Attempts=%d | Time=%.2fs",
        interview.id,
        len(attempts),
        time.perf_counter() - attempt_start,
    )
    logger.info(
        "POST /v2/summary-report assembling %d question attempts for interview_id=%s",
        len(attempts),
        interview.id,
    )

    analysis_start = time.perf_counter()

    logger.info(
        "Analysis started | Interview=%s | Attempts=%d",
        interview.id,
        len(attempts),
    )
    analyzed_count = await analysis_service.ensure_recorded_attempts_analyzed(
        question_attempts=attempts,
        user_id=current_user.id,
        db=session,
        logger=logger,
    )
    logger.info(
        "Analysis completed | Interview=%s | NewlyAnalyzed=%d | Time=%.2fs",
        interview.id,
        analyzed_count,
        time.perf_counter() - analysis_start,
    )
    if analyzed_count:
        attempts = await qa_repo.list_by_interview(interview_id=interview.id)
        logger.info(
            "Reloaded attempts after analysis | Interview=%s | Attempts=%d",
            interview.id,
            len(attempts),
        )

    # Check if resume was used for any questions in this interview
    from src.repository.crud.interview_question import InterviewQuestionCRUDRepository

    question_repo = InterviewQuestionCRUDRepository(session)
    question_start = time.perf_counter()
    questions = await question_repo.list_by_interview(interview_id=interview.id)
    resume_used = any(q.resume_used for q in questions) if questions else None

    logger.info(
        "Interview questions loaded | Interview=%s | Questions=%d | ResumeUsed=%s | Time=%.2fs",
        interview.id,
        len(questions),
        resume_used,
        time.perf_counter() - question_start,
    )
    # Get candidate name from user if available
    candidate_name = getattr(current_user, "name", None)

    service = SummaryReportServiceV2(session)
    report_start = time.perf_counter()

    logger.info(
        "Summary report generation started | Interview=%s",
        interview.id,
    )
    result = await service.generate_for_interview_lite(
        interview.id, attempts, interview.track, resume_used, candidate_name
    )
    logger.info(
        "Summary report generated | Interview=%s | Time=%.2fs",
        interview.id,
        time.perf_counter() - report_start,
    )

    # Persist summary report (idempotent per interview)
    try:
        save_start = time.perf_counter()
        await sr_repo.upsert(interview_id=interview.id, report_json=result)
        await session.commit()
        logger.info(
            "Summary report persisted | Interview=%s | Time=%.2fs",
            interview.id,
            time.perf_counter() - save_start,
        )
        logger.info(
            "POST /v2/summary-report persisted successfully for interview_id=%s",
            interview.id,
        )
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        logger.exception(
            "POST /v2/summary-report failed to persist for interview_id=%s: %s",
            interview.id,
            exc,
        )

    logger.info(
        "Summary Report V2 workflow completed | Interview=%s | TotalTime=%.2fs",
        interview.id,
        time.perf_counter() - request_start,
    )

    return SummaryReportResponseLite(**result)


@router.get(
    "/summary-report/{interview_id}",
    response_model=SummaryReportResponseLite,
    response_model_exclude_none=True,
    status_code=200,
    summary="Fetch a previously saved V2 summary report",
    description="Retrieves a persisted V2 Lite summary report for the interview if present.",
)
async def get_summary_report_v2(
    interview_id: int,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    interview_repo: InterviewCRUDRepository = Depends(
        get_repository(InterviewCRUDRepository)
    ),
    sr_repo: SummaryReportCRUDRepository = Depends(
        get_repository(SummaryReportCRUDRepository)
    ),
):
    logger = logging.getLogger(__name__)
    logger.info(
        "GET /v2/summary-report/%s by user_id=%s", interview_id, current_user.id
    )

    interview = await interview_repo.get_by_id_and_user(interview_id, current_user.id)
    if not interview:
        raise fastapi.HTTPException(
            status_code=404, detail="Interview not found or access denied"
        )

    record = await sr_repo.get_by_interview_id(interview_id)
    logger.info(
        "Summary report fetch | Interview=%s | Found=%s",
        interview_id,
        record is not None,
    )
    if not record or not record.report_json:
        raise fastapi.HTTPException(
            status_code=404, detail="Summary report not found for this interview"
        )

    await track_analytics_event(
        session,
        event_type="report_viewed",
        user_id=current_user.id,
        interview_id=interview.id,
        event_data={
            "report_type": "summary_v2",
            "source": "summary_report_v2.get_summary_report_v2",
        },
    )
    logger.info(
        "Report viewed analytics tracked | Interview=%s",
        interview_id,
    )

    return SummaryReportResponseLite(**record.report_json)
