"""Real local DB staging/locking/rollback acceptance, no real storage or AI calls."""
import asyncio
import hashlib
import uuid
import datetime
from types import SimpleNamespace
from fastapi import Response
from src.api.routes.resume import get_resume_storage_receipt
import sqlalchemy as sa
from src.repository.database import async_db
from src.models.db.resume_storage_job import ResumeStorageJob
from src.services.resume_storage_delivery import stage_resume_file, deliver_resume_files
from src.services import s3_service


async def main():
    async with async_db.get_session() as session:
        user_id=(await session.execute(sa.text('SELECT id FROM "user" ORDER BY id LIMIT 1'))).scalar_one()
    raw=("synthetic staging " + str(uuid.uuid4())).encode()
    fingerprint=hashlib.sha256(raw).hexdigest()
    original=s3_service._background_upload_and_enforce_limit
    entered, release=asyncio.Event(), asyncio.Event()
    calls=0
    async def upload(*args, **kwargs):
        nonlocal calls
        calls+=1
        assert args[0] == raw
        entered.set()
        await release.wait()
        return "synthetic-key"
    s3_service._background_upload_and_enforce_limit=upload
    task=None
    try:
        await stage_resume_file(raw,user_id,"synthetic.txt","text/plain","synthetic",fingerprint)
        async with async_db.get_session() as session:
            job=await session.get(ResumeStorageJob,(user_id,fingerprint))
            assert job.payload == raw and job.state == "pending"
        async with async_db.get_session() as session:
            receipt=await get_resume_storage_receipt(fingerprint,Response(),SimpleNamespace(id=user_id),session)
            assert receipt.status == "unconfirmed" and receipt.delivery_status == "pending"
            other=await get_resume_storage_receipt(fingerprint,Response(),SimpleNamespace(id=-1),session)
            assert other.status == "unconfirmed" and other.delivery_status is None
        task=asyncio.create_task(deliver_resume_files(user_id=user_id,fingerprint=fingerprint))
        await asyncio.wait_for(entered.wait(),10)
        assert await deliver_resume_files(user_id=user_id,fingerprint=fingerprint) == 0
        # Simulated cancellation before external success must roll back claim mutations.
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        async with async_db.get_session() as session:
            job=await session.get(ResumeStorageJob,(user_id,fingerprint))
            assert job.payload == raw and job.state == "pending" and job.attempts == 0
        release.set()
        assert await deliver_resume_files(user_id=user_id,fingerprint=fingerprint) == 1
        async with async_db.get_session() as session:
            job=await session.get(ResumeStorageJob,(user_id,fingerprint))
            assert job.payload is None and job.resume_text is None and job.state == "stored"
        async with async_db.get_session() as session:
            receipt=await get_resume_storage_receipt(fingerprint,Response(),SimpleNamespace(id=user_id),session)
            assert receipt.status == "unconfirmed" and receipt.delivery_status is None
            # Simulate an overdue pending job without running cleanup first.
            job=await session.get(ResumeStorageJob,(user_id,fingerprint))
            job.state="pending"
            job.expires_at=datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(seconds=1)
            await session.commit()
        async with async_db.get_session() as session:
            receipt=await get_resume_storage_receipt(fingerprint,Response(),SimpleNamespace(id=user_id),session)
            assert receipt.delivery_status == "expired"
        assert calls == 2
        print("PASS: committed original bytes, concurrent skip-locked claim, interrupted transaction rollback, subsequent delivery and payload removal; scoped delivery receipts, missing registry and overdue expiry; synthetic storage only")
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task,return_exceptions=True)
        s3_service._background_upload_and_enforce_limit=original
        async with async_db.get_session() as session:
            await session.execute(sa.delete(ResumeStorageJob).where(ResumeStorageJob.user_id==user_id,ResumeStorageJob.file_sha256==fingerprint))
            await session.commit()
