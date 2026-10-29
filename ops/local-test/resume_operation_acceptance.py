"""Real local PostgreSQL admission/replay test; no provider or profile writes."""
import asyncio
import uuid
import sqlalchemy as sa
from fastapi import HTTPException
from src.repository.database import async_db
from src.models.db.resume_operation import ResumeOperation
from src.services.resume_operation import run_resume_operation


async def main():
    key = str(uuid.uuid4())
    async with async_db.get_session() as session:
        user_id = (await session.execute(sa.text('SELECT id FROM "user" ORDER BY id LIMIT 1'))).scalar_one_or_none()
    if user_id is None:
        raise RuntimeError("Seed the isolated local database first")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def work():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return {"saved": True, "text_length": 3}
    first = asyncio.create_task(run_resume_operation(user_id, key, "a" * 64, work))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        try:
            await run_resume_operation(user_id, key, "a" * 64, work)
            raise AssertionError("Concurrent request was allowed")
        except HTTPException as error:
            assert error.status_code == 409
        release.set()
        receipt = await first
        assert await run_resume_operation(user_id, key, "a" * 64, work) == receipt
        try:
            await run_resume_operation(user_id, key, "b" * 64, work)
            raise AssertionError("Different bytes accepted under same key")
        except HTTPException as error:
            assert error.status_code == 409
        assert calls == 1
        print("PASS: real local PostgreSQL concurrent admission, committed replay and byte conflict; one synthetic work call")
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)
        async with async_db.get_session() as session:
            await session.execute(sa.delete(ResumeOperation).where(ResumeOperation.user_id == user_id, ResumeOperation.request_key == key))
            await session.commit()
