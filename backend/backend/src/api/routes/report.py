from __future__ import annotations

import logging
import fastapi
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession

from src.services.result_release import HELD_DETAIL, withheld
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.repository import get_repository
from src.api.dependencies.session import get_async_session
from src.models.db.user import User
from src.models.schemas.report import FinalReportRequest, FinalReportResponse
from src.repository.crud.interview import InterviewCRUDRepository
from src.repository.crud.question import QuestionAttemptCRUDRepository
from src.repository.crud.report import ReportCRUDRepository
from src.services.report import FinalReportService
from src.services.analytics_events import track_analytics_event


router = fastapi.APIRouter(tags=["report"])


@router.post(
    "/final-report",
    response_model=FinalReportResponse,
    status_code=200,
    summary="Generate and persist a final session report",
    description=(
        "Aggregates per-question analyses for the specified interview and returns a session-level report. "
        "Requires authentication and ownership of the interview. On success, persists the report."
    ),
)
async def generate_final_report(
    payload: FinalReportRequest,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    interview_repo: InterviewCRUDRepository = Depends(get_repository(InterviewCRUDRepository)),
    qa_repo: QuestionAttemptCRUDRepository = Depends(get_repository(QuestionAttemptCRUDRepository)),
    report_repo: ReportCRUDRepository = Depends(get_repository(ReportCRUDRepository)),
):
    logger = logging.getLogger(__name__)
    logger.info("/final-report called for interview_id=%s by user_id=%s", payload.interview_id, current_user.id)

    # Verify interview belongs to current user
    interview = await interview_repo.get_by_id_and_user(payload.interview_id, current_user.id)
    if not interview:
        raise fastapi.HTTPException(status_code=404, detail="Interview not found or access denied")

    # Fetch all question attempts for interview
    attempts = await qa_repo.list_by_interview(interview_id=interview.id)
    logger.debug("/final-report assembling %d question attempts for interview_id=%s", len(attempts), interview.id)

    # Generate report content
    service = FinalReportService(session)
    result = await service.generate_for_interview(interview.id, attempts)

    # Persist
    saved = True
    save_error: str | None = None
    try:
        await report_repo.upsert_report(
            interview_id=interview.id,
            summary=result["summary"],
            knowledge_competence=result["knowledge_competence"],
            speech_structure_fluency=result["speech_structure_fluency"],
            overall_score=result["overall_score"],
        )
        await session.commit()
        logger.info("/final-report persisted successfully for interview_id=%s", interview.id)
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        saved = False
        save_error = str(exc)
        logger.exception("/final-report failed to persist for interview_id=%s: %s", interview.id, exc)

    response = FinalReportResponse(
        interview_id=interview.id,
        summary=result["summary"],
        knowledge_competence=result["knowledge_competence"],
        speech_structure_fluency=result["speech_structure_fluency"],
        overall_score=result["overall_score"],
        saved=saved,
        save_error=save_error,
        message="Final report generated" if saved else "Final report generated but failed to save",
    )
    if withheld(interview):
        raise fastapi.HTTPException(status_code=409, detail=HELD_DETAIL)
    return response


@router.get(
    "/final-report/{interview_id}",
    response_model=FinalReportResponse,
    status_code=200,
    summary="Fetch a previously saved final session report",
    description=(
        "Retrieves a persisted final report for the interview if present. Requires authentication and ownership."
    ),
)
async def get_final_report(
    interview_id: int,
    current_user: User = Depends(get_current_user),
    session: SQLAlchemyAsyncSession = Depends(get_async_session),
    interview_repo: InterviewCRUDRepository = Depends(get_repository(InterviewCRUDRepository)),
    report_repo: ReportCRUDRepository = Depends(get_repository(ReportCRUDRepository)),
):
    logger = logging.getLogger(__name__)
    logger.info("GET /final-report/%s by user_id=%s", interview_id, current_user.id)

    # Verify interview belongs to current user
    interview = await interview_repo.get_by_id_and_user(interview_id, current_user.id)
    if not interview:
        raise fastapi.HTTPException(status_code=404, detail="Interview not found or access denied")

    # Fetch saved report
    report = await report_repo.get_by_interview_id(interview_id)
    if not report:
        raise fastapi.HTTPException(status_code=404, detail="Final report not found for this interview")

    await track_analytics_event(
        session,
        event_type="report_viewed",
        user_id=current_user.id,
        interview_id=interview.id,
        event_data={"report_type": "final", "source": "report.get_final_report"},
    )

    response = FinalReportResponse(
        interview_id=interview.id,
        summary=report.summary or {"title": "Interview Performance Summary", "overview": "", "per_question": []},
        knowledge_competence=report.knowledge_competence or {},
        speech_structure_fluency=report.speech_structure_fluency or {},
        overall_score=report.overall_score or 0.0,
        saved=True,
        save_error=None,
        message="Final report fetched",
    )
    if withheld(interview):
        raise fastapi.HTTPException(status_code=409, detail=HELD_DETAIL)
    return response
