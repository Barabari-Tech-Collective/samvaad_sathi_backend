"""At-most-once extraction dispatch for an account-owned operation key.

An interrupted 'started' operation is never automatically replayed: profile writes
may already have committed. Storage reconciliation remains a separate check.
"""
import uuid
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from fastapi import HTTPException
from src.models.db.resume_operation import ResumeOperation
from src.repository.database import async_db


def operation_key(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise HTTPException(422, "Invalid resume operation key") from None


def replay(row, fingerprint):
    if row.file_sha256 != fingerprint:
        raise HTTPException(409, "This operation key belongs to a different file.")
    if row.state == "completed" and isinstance(row.receipt, dict):
        return row.receipt
    if row.state == "failed":
        raise HTTPException(409, "This operation did not complete. Check your saved file before starting a new upload.")
    raise HTTPException(409, "This operation has already started; its outcome is not confirmed. Check its status before starting another upload.")


async def run_resume_operation(user_id, key, fingerprint, work):
    key = operation_key(key)
    async with async_db.get_session() as session:
        inserted = await session.execute(insert(ResumeOperation).values(
            user_id=user_id, request_key=key, file_sha256=fingerprint, state="started"
        ).on_conflict_do_nothing(index_elements=["user_id", "request_key"]).returning(ResumeOperation.request_key))
        claimed = inserted.scalar_one_or_none()
        await session.commit()  # Durable admission before any provider/profile side effect.
        if claimed is None:
            row = (await session.execute(sa.select(ResumeOperation).where(
                ResumeOperation.user_id == user_id, ResumeOperation.request_key == key
            ))).scalar_one()
            return replay(row, fingerprint)
    try:
        receipt = await work()
    except Exception:
        await finish(user_id, key, "failed", None)
        raise
    await finish(user_id, key, "completed", receipt)
    return receipt


async def finish(user_id, key, state, receipt):
    async with async_db.get_session() as session:
        await session.execute(sa.update(ResumeOperation).where(
            ResumeOperation.user_id == user_id, ResumeOperation.request_key == key,
            ResumeOperation.state == "started",
        ).values(state=state, receipt=receipt))
        await session.commit()


def recovery_key(user_id, row):
    """An explicit new attempt, never an automatic replay of ambiguous live work."""
    if row.state == "started":
        raise HTTPException(409, "The earlier operation is unresolved. It cannot be restarted safely.")
    if row.state == "completed" and (not isinstance(row.receipt, dict) or row.receipt.get("saved") is not False):
        raise HTTPException(409, "This operation does not qualify for another extraction attempt.")
    if row.state not in {"failed", "completed"}:
        raise HTTPException(409, "This operation cannot be retried.")
    # Repeat clicks resolve to one child key, including before its first dispatch.
    return str(uuid.uuid5(uuid.UUID(row.request_key), f"resume-recovery:{user_id}"))


def validate_storage_repair(row, raw, content_type):
    import hashlib
    if row.state != "completed" or not isinstance(row.receipt, dict) or row.receipt.get("saved") is not True:
        raise HTTPException(409, "Only a confirmed saved extraction can repair file storage.")
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(413, "Uploaded file exceeds 5 MB limit")
    if content_type != row.receipt.get("content_type"):
        raise HTTPException(409, "Select the original file with its original content type.")
    if content_type not in {"application/pdf", "text/plain"}:
        raise HTTPException(415, "Unsupported file type")
    if hashlib.sha256(raw).hexdigest() != row.file_sha256:
        raise HTTPException(409, "Select the exact file used for this extraction.")
