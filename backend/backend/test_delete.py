import asyncio
import sys
import traceback
sys.path.append('.')
from src.api.dependencies.session import get_async_session
from src.repository.crud.job_profile import JobProfileCRUDRepository

async def test_delete():
    async for session in get_async_session():
        repo = JobProfileCRUDRepository(session)
        try:
            print("Attempting to delete profile 70...")
            deleted = await repo.delete(job_profile_id=70)
            print(f"Delete result: {deleted}")
        except Exception as e:
            print("Failed to delete:")
            traceback.print_exc()
        break

if __name__ == "__main__":
    asyncio.run(test_delete())
