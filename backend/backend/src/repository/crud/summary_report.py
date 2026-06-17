from __future__ import annotations

from typing import Optional, List, Tuple
from datetime import datetime

from sqlalchemy import select, func, desc
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.models.db.summary_report import SummaryReport
from src.models.db.interview import Interview
from src.repository.crud.base import BaseCRUDRepository


class SummaryReportCRUDRepository(BaseCRUDRepository):
    async def get_by_interview_id(self, interview_id: int) -> Optional[SummaryReport]:
        stmt = select(SummaryReport).where(SummaryReport.interview_id == interview_id)
        res = await self.async_session.execute(stmt)
        return res.scalars().first()

    async def upsert(
        self,
        interview_id: int,
        report_json: dict | None,
    ) -> SummaryReport:
        stmt = (
            pg_insert(SummaryReport)
            .values(interview_id=interview_id, report_json=report_json)
            .on_conflict_do_update(index_elements=[SummaryReport.interview_id], set_={"report_json": report_json})
            .returning(SummaryReport)
        )
        res = await self.async_session.execute(stmt)
        summary_result = res.scalar_one()
        
        from src.services.analytics import _extract_overall_score
        from src.models.db.report import Report
        overall_score = _extract_overall_score(None, summary_result)
        if overall_score is not None:
            r_stmt = (
                pg_insert(Report)
                .values(interview_id=interview_id, overall_score=overall_score)
                .on_conflict_do_update(index_elements=[Report.interview_id], set_={"overall_score": overall_score})
            )
            await self.async_session.execute(r_stmt)
            
        return summary_result

    async def get_last_x_for_user(self, user_id: int, limit: int = 10) -> List[Tuple[SummaryReport, Interview]]:
        """
        Get the last x summary reports for a user, ordered by creation date (most recent first).
        
        Args:
            user_id: ID of the user
            limit: Maximum number of reports to return (default: 10)
            
        Returns:
            List of tuples containing (SummaryReport, Interview) objects
        """
        stmt = (
            select(SummaryReport, Interview)
            .join(Interview, SummaryReport.interview_id == Interview.id)
            .where(Interview.user_id == user_id)
            .order_by(desc(SummaryReport.created_at))
            .limit(limit)
        )
        
        res = await self.async_session.execute(stmt)
        return list(res.all())

    async def count_by_user(self, user_id: int) -> int:
        """
        Count total summary reports for a user.
        
        Args:
            user_id: ID of the user
            
        Returns:
            Total count of summary reports for the user
        """
        stmt = (
            select(func.count(SummaryReport.id))
            .join(Interview, SummaryReport.interview_id == Interview.id)
            .where(Interview.user_id == user_id)
        )
        
        res = await self.async_session.execute(stmt)
        return res.scalar() or 0