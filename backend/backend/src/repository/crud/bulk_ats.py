from __future__ import annotations

import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.db.bulk_ats import (
    BulkATSBatch,
    BulkATSCandidate,
    BulkATSAuditEvent,
    BulkATSDriveCredential,
)


class BulkATSCRUDRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # -- Batches ---------------------------------------------------------

    async def create_batch(
        self,
        *,
        name: str,
        role_name: str,
        role_description: str | None,
        evaluation_intent: str | None,
        required_skills: list[str] | None,
        preferred_skills: list[str] | None,
        experience_mode: str,
        job_profile_id: int | None,
        weight_overrides: dict | None,
        jd_analysis: dict | None,
        retention_until: datetime.datetime | None,
        created_by: int | None,
    ) -> BulkATSBatch:
        batch = BulkATSBatch(
            name=name,
            role_name=role_name,
            role_description=role_description,
            evaluation_intent=evaluation_intent,
            required_skills=required_skills,
            preferred_skills=preferred_skills,
            experience_mode=experience_mode,
            job_profile_id=job_profile_id,
            weight_overrides=weight_overrides,
            jd_analysis=jd_analysis,
            retention_until=retention_until,
            created_by=created_by,
        )
        self.session.add(batch)
        await self.session.commit()
        await self.session.refresh(batch)
        return batch

    async def get_batch(self, batch_id: int) -> BulkATSBatch | None:
        result = await self.session.execute(
            select(BulkATSBatch).where(BulkATSBatch.id == batch_id)
        )
        return result.scalar_one_or_none()

    async def list_batches(
        self, *, created_by: int | None = None, limit: int = 100, offset: int = 0
    ) -> list[BulkATSBatch]:
        query = (
            select(BulkATSBatch)
            .order_by(BulkATSBatch.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if created_by is not None:
            query = query.where(BulkATSBatch.created_by == created_by)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_expired_batches(
        self, *, now: datetime.datetime, limit: int = 50
    ) -> list[BulkATSBatch]:
        result = await self.session.execute(
            select(BulkATSBatch)
            .where(
                BulkATSBatch.retention_until.is_not(None),
                BulkATSBatch.retention_until <= now,
            )
            .order_by(BulkATSBatch.retention_until.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_batch_status(self, batch_id: int, status: str) -> None:
        batch = await self.get_batch(batch_id)
        if batch is not None:
            batch.status = status
            await self.session.commit()

    async def refresh_batch_status(self, batch_id: int) -> str | None:
        """Derive the batch lifecycle from its candidate rows.

        A batch is complete once every candidate reached a terminal state. A
        completely failed batch is marked failed; mixed success/failure is
        completed so recruiters can still inspect the usable results.
        """
        batch = await self.get_batch(batch_id)
        if batch is None:
            return None

        total = await self.count_candidates(batch_id=batch_id)
        if total == 0:
            status = "draft"
        else:
            completed = await self.count_candidates(
                batch_id=batch_id, status="completed"
            )
            needs_review = await self.count_candidates(
                batch_id=batch_id, status="needs_review"
            )
            failed = await self.count_candidates(batch_id=batch_id, status="failed")
            terminal = completed + needs_review + failed
            if terminal < total:
                status = "processing"
            elif failed == total:
                status = "failed"
            else:
                status = "completed"

        if batch.status != status:
            batch.status = status
            await self.session.commit()
        return status

    async def delete_batch(self, batch: BulkATSBatch) -> None:
        await self.session.delete(batch)
        await self.session.commit()

    # -- Candidates --------------------------------------------------------

    async def create_candidate(self, candidate: BulkATSCandidate) -> BulkATSCandidate:
        self.session.add(candidate)
        await self.session.commit()
        await self.session.refresh(candidate)
        return candidate

    async def get_candidate(self, candidate_id: int) -> BulkATSCandidate | None:
        # Eager-loads batch: callers (notably the worker task) read
        # candidate.batch.role_name/experience_mode, and async SQLAlchemy
        # raises on an unawaited lazy load rather than fetching it for you.
        result = await self.session.execute(
            select(BulkATSCandidate)
            .options(selectinload(BulkATSCandidate.batch))
            .where(BulkATSCandidate.id == candidate_id)
        )
        return result.scalar_one_or_none()

    async def find_duplicate(
        self, *, batch_id: int, file_sha256: str | None, candidate_key: str | None
    ) -> BulkATSCandidate | None:
        """Per-batch dedup by file hash (identical file re-upload) OR
        candidate_key (same email/candidate-id/resume URL re-import)."""
        if file_sha256:
            result = await self.session.execute(
                select(BulkATSCandidate).where(
                    BulkATSCandidate.batch_id == batch_id,
                    BulkATSCandidate.file_sha256 == file_sha256,
                )
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                return existing
        if candidate_key:
            result = await self.session.execute(
                select(BulkATSCandidate).where(
                    BulkATSCandidate.batch_id == batch_id,
                    BulkATSCandidate.candidate_key == candidate_key,
                )
            )
            return result.scalar_one_or_none()
        return None

    async def list_candidates(
        self,
        *,
        batch_id: int,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort: str = "score",
    ) -> list[BulkATSCandidate]:
        query = select(BulkATSCandidate).where(BulkATSCandidate.batch_id == batch_id)
        if status:
            query = query.where(BulkATSCandidate.status == status)
        if sort == "confidence":
            query = query.order_by(
                BulkATSCandidate.confidence_score.asc().nullsfirst(),
                BulkATSCandidate.final_score.desc().nullslast(),
            )
        elif sort == "review_priority":
            query = query.order_by(
                BulkATSCandidate.needs_review.desc(),
                BulkATSCandidate.confidence_score.asc().nullsfirst(),
                BulkATSCandidate.final_score.desc().nullslast(),
            )
        elif sort == "score":
            query = query.order_by(
                BulkATSCandidate.final_score.desc().nullslast(),
                BulkATSCandidate.created_at.asc(),
            )
        else:
            query = query.order_by(BulkATSCandidate.created_at.asc())
        query = query.limit(limit).offset(offset)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_candidates(
        self, *, batch_id: int, status: str | None = None
    ) -> int:
        query = (
            select(func.count())
            .select_from(BulkATSCandidate)
            .where(BulkATSCandidate.batch_id == batch_id)
        )
        if status:
            query = query.where(BulkATSCandidate.status == status)
        result = await self.session.execute(query)
        return int(result.scalar() or 0)

    async def update_candidate_status(
        self, candidate_id: int, status: str, error_message: str | None = None
    ) -> None:
        candidate = await self.get_candidate(candidate_id)
        if candidate is not None:
            candidate.status = status
            candidate.error_message = error_message
            await self.session.commit()

    async def save_candidate_result(
        self, candidate_id: int, **fields
    ) -> BulkATSCandidate | None:
        candidate = await self.get_candidate(candidate_id)
        if candidate is None:
            return None
        for key, value in fields.items():
            setattr(candidate, key, value)
        await self.session.commit()
        await self.session.refresh(candidate)
        return candidate

    async def update_review(
        self,
        candidate_id: int,
        *,
        reviewer_notes: str,
        reviewer_decision: str | None,
        decision_reason: str | None,
        interview_outcome: str | None,
        reviewed_by: int,
    ) -> BulkATSCandidate | None:
        candidate = await self.get_candidate(candidate_id)
        if candidate is None:
            return None
        candidate.reviewer_notes = reviewer_notes
        candidate.reviewer_decision = reviewer_decision
        candidate.decision_reason = decision_reason
        if candidate.interview_outcome != interview_outcome:
            candidate.outcome_recorded_at = (
                datetime.datetime.now(datetime.timezone.utc)
                if interview_outcome
                else None
            )
        candidate.interview_outcome = interview_outcome
        candidate.reviewed_by = reviewed_by
        await self.session.commit()
        await self.session.refresh(candidate)
        return candidate

    # -- Audit -------------------------------------------------------------

    async def record_audit(
        self,
        *,
        action: str,
        actor_id: int | None,
        batch_id: int | None = None,
        candidate_id: int | None = None,
        metadata: dict | None = None,
    ) -> BulkATSAuditEvent:
        event = BulkATSAuditEvent(
            action=action,
            actor_id=actor_id,
            batch_id=batch_id,
            candidate_id=candidate_id,
            event_metadata=metadata,
        )
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(event)
        return event

    async def list_audit_events(
        self, *, batch_id: int, limit: int = 200
    ) -> list[BulkATSAuditEvent]:
        result = await self.session.execute(
            select(BulkATSAuditEvent)
            .where(BulkATSAuditEvent.batch_id == batch_id)
            .order_by(BulkATSAuditEvent.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # -- Google Drive OAuth credentials -----------------------------------

    async def get_drive_credential(
        self, *, user_id: int
    ) -> BulkATSDriveCredential | None:
        result = await self.session.execute(
            select(BulkATSDriveCredential).where(
                BulkATSDriveCredential.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def upsert_drive_credential(
        self,
        *,
        user_id: int,
        encrypted_refresh_token: str,
        google_email: str | None,
        scopes: list[str],
        key_id: str,
    ) -> BulkATSDriveCredential:
        credential = await self.get_drive_credential(user_id=user_id)
        if credential is None:
            credential = BulkATSDriveCredential(user_id=user_id)
            self.session.add(credential)
        credential.encrypted_refresh_token = encrypted_refresh_token
        credential.google_email = google_email
        credential.scopes = scopes
        credential.key_id = key_id
        await self.session.commit()
        await self.session.refresh(credential)
        return credential

    async def mark_drive_credential_used(self, *, user_id: int) -> None:
        credential = await self.get_drive_credential(user_id=user_id)
        if credential is not None:
            credential.last_used_at = datetime.datetime.now(datetime.timezone.utc)
            await self.session.commit()

    async def delete_drive_credential(self, *, user_id: int) -> bool:
        credential = await self.get_drive_credential(user_id=user_id)
        if credential is None:
            return False
        await self.session.delete(credential)
        await self.session.commit()
        return True
