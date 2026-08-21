import sqlalchemy
from typing import List, Tuple, Optional
import datetime

from src.models.db.interview import Interview
from src.models.db.question_attempt import QuestionAttempt
from src.models.db.summary_report import SummaryReport
from src.repository.crud.base import BaseCRUDRepository


class InterviewCRUDRepository(BaseCRUDRepository):
    async def get_active_by_user(self, *, user_id: int) -> Interview | None:
        stmt = (
            sqlalchemy.select(Interview)
            .where(Interview.user_id == user_id)
            .where(Interview.status == "active")
            .order_by(Interview.id.desc())
        )
        query = await self.async_session.execute(statement=stmt)
        return query.scalar()  # type: ignore

    async def create_interview(self, *, user_id: int, track: str, difficulty: str = "medium", job_profile_id: int | None = None) -> Interview:
        new_interview = Interview(user_id=user_id, track=track, difficulty=difficulty, status="active", job_profile_id=job_profile_id)
        self.async_session.add(new_interview)
        await self.async_session.commit()
        await self.async_session.refresh(new_interview)
        return new_interview

    async def mark_completed(self, *, interview_id: int) -> Interview | None:
        stmt = sqlalchemy.select(Interview).where(Interview.id == interview_id)
        query = await self.async_session.execute(statement=stmt)
        interview: Interview | None = query.scalar()  # type: ignore
        if not interview:
            return None

        duration_stmt = (
            sqlalchemy.select(
                sqlalchemy.func.min(QuestionAttempt.created_at).label("start_at"),
                sqlalchemy.func.max(QuestionAttempt.created_at).label("end_at"),
            )
            .where(QuestionAttempt.interview_id == interview_id)
        )
        duration_row = (await self.async_session.execute(duration_stmt)).one()
        start_at = duration_row.start_at
        end_at = duration_row.end_at

        duration_seconds: int | None = None
        if start_at is not None and end_at is not None:
            duration_seconds = max(0, int((end_at - start_at).total_seconds()))

        interview.status = "completed"
        interview.completed_at = datetime.datetime.now(datetime.timezone.utc)
        interview.duration_seconds = duration_seconds
        await self.async_session.commit()
        await self.async_session.refresh(interview)
        return interview

    async def list_by_user_cursor(self, *, user_id: int, limit: int, cursor_id: int | None) -> tuple[list[Tuple[Interview, List[SummaryReport], bool]], int | None]:
        """Get user's interviews with summary reports and resume_used flag."""
        # Build the base query for interviews
        stmt = sqlalchemy.select(Interview).where(Interview.user_id == user_id)
        if cursor_id is not None:
            stmt = stmt.where(Interview.id < cursor_id)
        stmt = stmt.order_by(Interview.id.desc()).limit(limit + 1)
        
        query = await self.async_session.execute(statement=stmt)
        interviews = list(query.scalars().all())
        
        # Determine next cursor
        next_cursor: int | None = None
        if len(interviews) > limit:
            next_cursor = interviews[-1].id
            interviews = interviews[:limit]
        
        # Get all summary reports for these interviews
        interview_ids = [interview.id for interview in interviews]
        summary_stmt = sqlalchemy.select(SummaryReport).where(SummaryReport.interview_id.in_(interview_ids)).order_by(SummaryReport.created_at.desc())
        summary_query = await self.async_session.execute(statement=summary_stmt)
        all_summary_reports = list(summary_query.scalars().all())
        
        # Group summary reports by interview_id
        summary_reports_by_interview = {}
        for sr in all_summary_reports:
            if sr.interview_id not in summary_reports_by_interview:
                summary_reports_by_interview[sr.interview_id] = []
            summary_reports_by_interview[sr.interview_id].append(sr)
        
        # Get resume_used flag for each interview (from first question if exists)
        from src.models.db.interview_question import InterviewQuestion
        resume_used_stmt = sqlalchemy.select(InterviewQuestion.interview_id, InterviewQuestion.resume_used).where(
            InterviewQuestion.interview_id.in_(interview_ids)
        ).distinct(InterviewQuestion.interview_id)
        resume_used_query = await self.async_session.execute(statement=resume_used_stmt)
        resume_used_flags = {row[0]: row[1] for row in resume_used_query.fetchall()}
        
        # Combine interviews with their summary reports and resume_used flag
        result = []
        for interview in interviews:
            summary_reports = summary_reports_by_interview.get(interview.id, [])
            resume_used = resume_used_flags.get(interview.id, False)
            result.append((interview, summary_reports, resume_used))
        
        return result, next_cursor

    async def get_by_id(self, *, interview_id: int) -> Interview | None:
        stmt = sqlalchemy.select(Interview).where(Interview.id == interview_id)
        query = await self.async_session.execute(statement=stmt)
        return query.scalar()  # type: ignore

    async def get_by_id_and_user(self, interview_id: int, user_id: int) -> Interview | None:
        stmt = (
            sqlalchemy.select(Interview)
            .where(Interview.id == interview_id)
            .where(Interview.user_id == user_id)
        )
        query = await self.async_session.execute(statement=stmt)
        return query.scalar()  # type: ignore

    async def list_by_user_cursor_with_summary(self, *, user_id: int, limit: int, cursor_id: int | None) -> tuple[list[Tuple[Interview, List[SummaryReport]]], int | None]:
        """
        Get user's interviews with all their associated summary reports.
        
        Args:
            user_id: ID of the user
            limit: Maximum number of interviews to return
            cursor_id: Cursor for pagination
            
        Returns:
            Tuple of (list of (Interview, List[SummaryReport]), next_cursor)
        """
        # Build the base query for interviews
        stmt = sqlalchemy.select(Interview).where(Interview.user_id == user_id)
        if cursor_id is not None:
            stmt = stmt.where(Interview.id < cursor_id)
        stmt = stmt.order_by(Interview.id.desc()).limit(limit + 1)
        
        query = await self.async_session.execute(statement=stmt)
        interviews = list(query.scalars().all())
        
        # Determine next cursor
        next_cursor: int | None = None
        if len(interviews) > limit:
            next_cursor = interviews[-1].id
            interviews = interviews[:limit]
        
        # Get all summary reports for these interviews
        interview_ids = [interview.id for interview in interviews]
        summary_stmt = sqlalchemy.select(SummaryReport).where(SummaryReport.interview_id.in_(interview_ids)).order_by(SummaryReport.created_at.desc())
        summary_query = await self.async_session.execute(statement=summary_stmt)
        all_summary_reports = list(summary_query.scalars().all())
        
        # Group summary reports by interview_id
        summary_reports_by_interview = {}
        for sr in all_summary_reports:
            if sr.interview_id not in summary_reports_by_interview:
                summary_reports_by_interview[sr.interview_id] = []
            summary_reports_by_interview[sr.interview_id].append(sr)
        
        # Combine interviews with their summary reports (ordered by creation date desc)
        result = [(interview, summary_reports_by_interview.get(interview.id, [])) for interview in interviews]
        
        return result, next_cursor


