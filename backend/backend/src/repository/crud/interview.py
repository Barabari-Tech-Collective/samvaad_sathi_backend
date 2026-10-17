import datetime
from typing import List, Optional, Tuple

import sqlalchemy

from src.models.db.interview import Interview
from src.models.db.question_attempt import QuestionAttempt
from src.models.db.summary_report import SummaryReport
from src.repository.crud.base import BaseCRUDRepository
from src.services.interview_lifecycle import interview_has_expired
from src.services.interview_session_lease import (
    SESSION_LEASE_TTL,
    new_session_lease_token,
    session_lease_digest,
    session_lease_is_active,
    session_lease_matches,
)

PRACTICE_SESSION_TTL = datetime.timedelta(days=7)


class InterviewCRUDRepository(BaseCRUDRepository):
    async def lock_by_id_and_user(
        self, *, interview_id: int, user_id: int
    ) -> Interview | None:
        stmt = (
            sqlalchemy.select(Interview)
            .where(Interview.id == interview_id, Interview.user_id == user_id)
            .with_for_update()
        )
        return (await self.async_session.execute(stmt)).scalar_one_or_none()

    async def claim_session_lease(
        self,
        *,
        interview_id: int,
        user_id: int,
        presented_token: str | None,
        force: bool,
    ) -> tuple[Interview | None, str, str | None]:
        interview = await self.lock_by_id_and_user(
            interview_id=interview_id, user_id=user_id
        )
        if interview is None:
            return None, "not_found", None
        if interview.interview_mode != "practice":
            return interview, "not_practice", None
        if interview.status != "active":
            return interview, "inactive", None
        if interview_has_expired(interview):
            return interview, "expired", None

        now = datetime.datetime.now(datetime.timezone.utc)
        if force:
            token = new_session_lease_token()
            state = (
                "taken_over"
                if getattr(interview, "session_lease_hash", None)
                else "acquired"
            )
            interview.session_lease_hash = session_lease_digest(token)
            interview.session_lease_acquired_at = now
        elif session_lease_matches(interview, presented_token):
            token = presented_token
            state = "renewed"
        elif session_lease_is_active(interview, now=now) and not force:
            return interview, "conflict", None
        else:
            token = new_session_lease_token()
            state = "acquired"
            interview.session_lease_hash = session_lease_digest(token)
            interview.session_lease_acquired_at = now

        interview.session_lease_expires_at = now + SESSION_LEASE_TTL
        interview.session_lease_required = True
        await self.async_session.commit()
        await self.async_session.refresh(interview)
        return interview, state, token

    async def heartbeat_session_lease(
        self,
        *,
        interview_id: int,
        user_id: int,
        lease_token: str,
    ) -> Interview | None:
        interview = await self.lock_by_id_and_user(
            interview_id=interview_id, user_id=user_id
        )
        if (
            interview is None
            or interview.status != "active"
            or interview.interview_mode != "practice"
            or interview_has_expired(interview)
            or not session_lease_matches(interview, lease_token)
        ):
            return None
        interview.session_lease_expires_at = (
            datetime.datetime.now(datetime.timezone.utc) + SESSION_LEASE_TTL
        )
        await self.async_session.commit()
        await self.async_session.refresh(interview)
        return interview

    @staticmethod
    def clear_session_lease(interview: Interview) -> None:
        interview.session_lease_hash = None
        interview.session_lease_acquired_at = None
        interview.session_lease_expires_at = None

    async def get_active_by_user(self, *, user_id: int) -> Interview | None:
        stmt = (
            sqlalchemy.select(Interview)
            .where(Interview.user_id == user_id)
            .where(Interview.status == "active")
            .order_by(Interview.id.desc())
        )
        query = await self.async_session.execute(statement=stmt)
        return query.scalar()  # type: ignore

    async def get_resumable_by_user(self, *, user_id: int) -> Interview | None:
        now = datetime.datetime.now(datetime.timezone.utc)
        stmt = (
            sqlalchemy.select(Interview)
            .where(Interview.user_id == user_id)
            .where(Interview.status.in_(("active", "paused")))
            .where(
                sqlalchemy.or_(
                    Interview.expires_at.is_(None), Interview.expires_at > now
                )
            )
            .order_by(Interview.id.desc())
        )
        query = await self.async_session.execute(statement=stmt)
        return query.scalar()  # type: ignore

    async def create_interview(
        self,
        *,
        user_id: int,
        track: str,
        difficulty: str = "medium",
        job_profile_id: int | None = None,
    ) -> Interview:
        # Derived here, not accepted as a parameter, so every caller - v1, v2,
        # the non-tech flow - gets a correct snapshot without each having to
        # remember to look it up and pass it through themselves.
        interview_mode = "practice"
        if job_profile_id is not None:
            from src.models.db.job_profile import JobProfile

            profile = await self.async_session.get(JobProfile, job_profile_id)
            if profile is not None:
                interview_mode = profile.interview_mode

        new_interview = Interview(
            user_id=user_id,
            track=track,
            difficulty=difficulty,
            status="active",
            job_profile_id=job_profile_id,
            interview_mode=interview_mode,
            expires_at=(
                datetime.datetime.now(datetime.timezone.utc) + PRACTICE_SESSION_TTL
                if interview_mode == "practice"
                else None
            ),
        )
        self.async_session.add(new_interview)
        await self.async_session.commit()
        await self.async_session.refresh(new_interview)
        return new_interview

    async def mark_paused(self, *, interview_id: int) -> Interview | None:
        now = datetime.datetime.now(datetime.timezone.utc)
        stmt = (
            sqlalchemy.update(Interview)
            .where(Interview.id == interview_id, Interview.status == "active")
            .values(
                status="paused",
                state_version=Interview.state_version + 1,
                paused_at=now,
                expires_at=now + PRACTICE_SESSION_TTL,
                session_lease_hash=None,
                session_lease_acquired_at=None,
                session_lease_expires_at=None,
            )
            .returning(Interview)
        )
        interview = (await self.async_session.execute(stmt)).scalar_one_or_none()
        await self.async_session.commit()
        if interview is not None:
            await self.async_session.refresh(interview)
        return interview

    async def mark_resumed(self, *, interview_id: int) -> Interview | None:
        now = datetime.datetime.now(datetime.timezone.utc)
        stmt = (
            sqlalchemy.update(Interview)
            .where(Interview.id == interview_id, Interview.status == "paused")
            .values(
                status="active",
                state_version=Interview.state_version + 1,
                paused_at=None,
                expires_at=now + PRACTICE_SESSION_TTL,
                session_lease_hash=None,
                session_lease_acquired_at=None,
                session_lease_expires_at=None,
            )
            .returning(Interview)
        )
        interview = (await self.async_session.execute(stmt)).scalar_one_or_none()
        await self.async_session.commit()
        if interview is not None:
            await self.async_session.refresh(interview)
        return interview

    async def mark_expired(self, *, interview_id: int) -> Interview | None:
        stmt = (
            sqlalchemy.update(Interview)
            .where(
                Interview.id == interview_id, Interview.status.in_(("active", "paused"))
            )
            .values(
                status="expired",
                state_version=Interview.state_version + 1,
                paused_at=None,
                session_lease_hash=None,
                session_lease_acquired_at=None,
                session_lease_expires_at=None,
            )
            .returning(Interview)
        )
        interview = (await self.async_session.execute(stmt)).scalar_one_or_none()
        await self.async_session.commit()
        if interview is not None:
            await self.async_session.refresh(interview)
        return interview

    async def mark_completed(self, *, interview_id: int) -> Interview | None:
        stmt = sqlalchemy.select(Interview).where(Interview.id == interview_id)
        query = await self.async_session.execute(statement=stmt)
        interview: Interview | None = query.scalar()  # type: ignore
        if not interview:
            return None

        duration_stmt = sqlalchemy.select(
            sqlalchemy.func.min(QuestionAttempt.created_at).label("start_at"),
            sqlalchemy.func.max(QuestionAttempt.created_at).label("end_at"),
        ).where(QuestionAttempt.interview_id == interview_id)
        duration_row = (await self.async_session.execute(duration_stmt)).one()
        start_at = duration_row.start_at
        end_at = duration_row.end_at

        duration_seconds: int | None = None
        if start_at is not None and end_at is not None:
            duration_seconds = max(0, int((end_at - start_at).total_seconds()))

        interview.status = "completed"
        interview.state_version = int(interview.state_version or 0) + 1
        interview.completed_at = datetime.datetime.now(datetime.timezone.utc)
        interview.duration_seconds = duration_seconds
        self.clear_session_lease(interview)
        await self.async_session.commit()
        await self.async_session.refresh(interview)
        return interview

    async def mark_terminated_for_proctoring(
        self, *, interview_id: int, reason: str
    ) -> Interview | None:
        """End an interview early because the student left the tab or fullscreen.

        Mirrors mark_completed's duration calculation - a terminated sitting
        can still have real answered questions worth timing - but status is
        "terminated", never "completed", so nothing downstream can mistake an
        ended attempt for a finished one.
        """
        stmt = sqlalchemy.select(Interview).where(Interview.id == interview_id)
        query = await self.async_session.execute(statement=stmt)
        interview: Interview | None = query.scalar()  # type: ignore
        if not interview:
            return None

        duration_stmt = sqlalchemy.select(
            sqlalchemy.func.min(QuestionAttempt.created_at).label("start_at"),
            sqlalchemy.func.max(QuestionAttempt.created_at).label("end_at"),
        ).where(QuestionAttempt.interview_id == interview_id)
        duration_row = (await self.async_session.execute(duration_stmt)).one()
        start_at = duration_row.start_at
        end_at = duration_row.end_at

        duration_seconds: int | None = None
        if start_at is not None and end_at is not None:
            duration_seconds = max(0, int((end_at - start_at).total_seconds()))

        interview.status = "terminated"
        interview.completed_at = datetime.datetime.now(datetime.timezone.utc)
        interview.duration_seconds = duration_seconds
        interview.proctoring_terminated = True
        interview.proctoring_violation_reason = reason
        await self.async_session.commit()
        await self.async_session.refresh(interview)
        return interview

    async def list_by_user_cursor(
        self, *, user_id: int, limit: int, cursor_id: int | None
    ) -> tuple[list[Tuple[Interview, List[SummaryReport], bool]], int | None]:
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
        summary_stmt = (
            sqlalchemy.select(SummaryReport)
            .where(SummaryReport.interview_id.in_(interview_ids))
            .order_by(SummaryReport.created_at.desc())
        )
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

        resume_used_stmt = (
            sqlalchemy.select(
                InterviewQuestion.interview_id, InterviewQuestion.resume_used
            )
            .where(InterviewQuestion.interview_id.in_(interview_ids))
            .distinct(InterviewQuestion.interview_id)
        )
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

    async def get_by_id_and_user(
        self, interview_id: int, user_id: int
    ) -> Interview | None:
        stmt = (
            sqlalchemy.select(Interview)
            .where(Interview.id == interview_id)
            .where(Interview.user_id == user_id)
        )
        query = await self.async_session.execute(statement=stmt)
        return query.scalar()  # type: ignore

    async def list_by_user_cursor_with_summary(
        self, *, user_id: int, limit: int, cursor_id: int | None
    ) -> tuple[list[Tuple[Interview, List[SummaryReport]]], int | None]:
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
        summary_stmt = (
            sqlalchemy.select(SummaryReport)
            .where(SummaryReport.interview_id.in_(interview_ids))
            .order_by(SummaryReport.created_at.desc())
        )
        summary_query = await self.async_session.execute(statement=summary_stmt)
        all_summary_reports = list(summary_query.scalars().all())

        # Group summary reports by interview_id
        summary_reports_by_interview = {}
        for sr in all_summary_reports:
            if sr.interview_id not in summary_reports_by_interview:
                summary_reports_by_interview[sr.interview_id] = []
            summary_reports_by_interview[sr.interview_id].append(sr)

        # Combine interviews with their summary reports (ordered by creation date desc)
        result = [
            (interview, summary_reports_by_interview.get(interview.id, []))
            for interview in interviews
        ]

        return result, next_cursor

    async def count_user_interviews_by_profile_and_difficulty(
        self,
        user_id: int,
        job_profile_id: int,
        difficulty: str,
        current_interview_id: int,
    ) -> int:
        """Counts the number of previous interviews the user has created for this profile/difficulty."""
        stmt = (
            sqlalchemy.select(sqlalchemy.func.count(Interview.id))
            .where(Interview.user_id == user_id)
            .where(Interview.job_profile_id == job_profile_id)
            .where(Interview.difficulty == difficulty)
            .where(Interview.id < current_interview_id)
        )
        query = await self.async_session.execute(statement=stmt)
        count = query.scalar()
        return count or 0
