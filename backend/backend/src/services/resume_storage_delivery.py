"""Database-backed, short-lived storage delivery. Never invokes AI or profile writes."""
import datetime
import hashlib
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from src.models.db.resume_storage_job import ResumeStorageJob
from src.repository.database import async_db


async def stage_resume_file(raw, user_id, filename, content_type, text, fingerprint):
    if not raw or len(raw) > 5 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != fingerprint:
        raise ValueError("Invalid resume staging payload")
    expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
    async with async_db.get_session() as session:
        # No reset of pending attempts on repeated extraction of the same file.
        statement = insert(ResumeStorageJob).values(user_id=user_id,file_sha256=fingerprint,
            payload=raw,filename=(filename or "resume.pdf")[:256],content_type=content_type,
            resume_text=text,state="pending",attempts=0,expires_at=expires)
        await session.execute(statement.on_conflict_do_update(
            index_elements=["user_id","file_sha256"],
            set_={"payload":raw,"filename":(filename or "resume.pdf")[:256],"content_type":content_type,
                  "resume_text":text,"state":"pending","attempts":0,"expires_at":expires},
            where=ResumeStorageJob.state.in_(["stored","expired"])))
        await session.commit()


async def deliver_resume_files(ctx=None, *, user_id=None, fingerprint=None):
    from src.services.s3_service import _background_upload_and_enforce_limit
    if (user_id is None) != (fingerprint is None):
        raise ValueError("Both account and fingerprint are required for scoped delivery")
    scope = []
    if user_id is not None and fingerprint is not None:
        scope = [ResumeStorageJob.user_id == user_id, ResumeStorageJob.file_sha256 == fingerprint]
    now = datetime.datetime.now(datetime.timezone.utc)
    # Clean up expired payloads even when delivery has exhausted its retry budget.
    async with async_db.get_session() as session:
        await session.execute(sa.update(ResumeStorageJob).where(
            *scope, ResumeStorageJob.expires_at <= now,
            ResumeStorageJob.state.in_(["pending","failed"]),
        ).values(payload=None,resume_text=None,state="expired"))
        await session.commit()
    processed = 0
    # One bounded batch. Row locks prevent two delivery workers claiming the same file.
    async with async_db.get_session() as session:
        rows = (await session.execute(sa.select(ResumeStorageJob).where(
            *scope, ResumeStorageJob.state == "pending", ResumeStorageJob.expires_at > now,
            ResumeStorageJob.attempts < 3,
        ).order_by(ResumeStorageJob.expires_at).limit(5).with_for_update(skip_locked=True))).scalars().all()
        for row in rows:
            row.attempts += 1
            try:
                if not row.payload or hashlib.sha256(row.payload).hexdigest() != row.file_sha256:
                    raise ValueError("Invalid staged bytes")
                await _background_upload_and_enforce_limit(row.payload,row.user_id,row.filename,
                    row.content_type,row.resume_text,"onboarding",len(row.payload),row.file_sha256,
                    durable_delivery=True)
            except Exception:
                if row.attempts >= 3:
                    row.state = "failed"
                    row.payload = None
                    row.resume_text = None
            else:
                row.state = "stored"
                row.payload = None
                row.resume_text = None
            processed += 1
        await session.commit()
    return processed
