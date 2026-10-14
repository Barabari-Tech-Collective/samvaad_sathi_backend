from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.db.bulk_ats import BulkATSBatch, BulkATSCandidate


class BulkATSCRUDRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # -- Batches ---------------------------------------------------------

    async def create_batch(
        self,
        *,
        name: str,
        role_name: str,
        experience_mode: str,
        job_profile_id: int | None,
        weight_overrides: dict | None,
        created_by: int | None,
    ) -> BulkATSBatch:
        batch = BulkATSBatch(
            name=name,
            role_name=role_name,
            experience_mode=experience_mode,
            job_profile_id=job_profile_id,
            weight_overrides=weight_overrides,
            created_by=created_by,
        )
        self.session.add(batch)
        await self.session.commit()
        await self.session.refresh(batch)
        return batch

    async def get_batch(self, batch_id: int) -> BulkATSBatch | None:
        result = await self.session.execute(select(BulkATSBatch).where(BulkATSBatch.id == batch_id))
        return result.scalar_one_or_none()

    async def list_batches(self, *, created_by: int | None = None, limit: int = 100, offset: int = 0) -> list[BulkATSBatch]:
        query = select(BulkATSBatch).order_by(BulkATSBatch.created_at.desc()).limit(limit).offset(offset)
        if created_by is not None:
            query = query.where(BulkATSBatch.created_by == created_by)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update_batch_status(self, batch_id: int, status: str) -> None:
        batch = await self.get_batch(batch_id)
        if batch is not None:
            batch.status = status
            await self.session.commit()

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
                    BulkATSCandidate.batch_id == batch_id, BulkATSCandidate.file_sha256 == file_sha256
                )
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                return existing
        if candidate_key:
            result = await self.session.execute(
                select(BulkATSCandidate).where(
                    BulkATSCandidate.batch_id == batch_id, BulkATSCandidate.candidate_key == candidate_key
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
        order_by_score_desc: bool = True,
    ) -> list[BulkATSCandidate]:
        query = select(BulkATSCandidate).where(BulkATSCandidate.batch_id == batch_id)
        if status:
            query = query.where(BulkATSCandidate.status == status)
        if order_by_score_desc:
            query = query.order_by(BulkATSCandidate.final_score.desc().nullslast(), BulkATSCandidate.created_at.asc())
        else:
            query = query.order_by(BulkATSCandidate.created_at.asc())
        query = query.limit(limit).offset(offset)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_candidates(self, *, batch_id: int, status: str | None = None) -> int:
        query = select(func.count()).select_from(BulkATSCandidate).where(BulkATSCandidate.batch_id == batch_id)
        if status:
            query = query.where(BulkATSCandidate.status == status)
        result = await self.session.execute(query)
        return int(result.scalar() or 0)

    async def update_candidate_status(self, candidate_id: int, status: str, error_message: str | None = None) -> None:
        candidate = await self.get_candidate(candidate_id)
        if candidate is not None:
            candidate.status = status
            if error_message is not None:
                candidate.error_message = error_message
            await self.session.commit()

    async def save_candidate_result(self, candidate_id: int, **fields) -> BulkATSCandidate | None:
        candidate = await self.get_candidate(candidate_id)
        if candidate is None:
            return None
        for key, value in fields.items():
            setattr(candidate, key, value)
        await self.session.commit()
        await self.session.refresh(candidate)
        return candidate

    async def update_review(self, candidate_id: int, *, reviewer_notes: str, reviewed_by: int) -> BulkATSCandidate | None:
        candidate = await self.get_candidate(candidate_id)
        if candidate is None:
            return None
        candidate.reviewer_notes = reviewer_notes
        candidate.reviewed_by = reviewed_by
        await self.session.commit()
        await self.session.refresh(candidate)
        return candidate
