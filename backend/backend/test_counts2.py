import asyncio
import sys
import traceback
sys.path.append('.')
from src.api.dependencies.session import get_async_session
from src.repository.crud.job_profile import JobProfileCRUDRepository
from src.models.schemas.job_profile import JobProfileListResponse

async def test_counts():
    async for session in get_async_session():
        repo = JobProfileCRUDRepository(session)
        profiles = await repo.list_profiles()
        print(f"Loaded {len(profiles)} profiles.")
        try:
            # test list response
            list_response = JobProfileListResponse(items=profiles, total=len(profiles))
            print("JobProfileListResponse created successfully!")
            
            # Print the first few to check counts
            for item in list_response.items[:1]:
                print(item.model_dump_json(indent=2))
                
        except Exception as e:
            print("Failed to create JobProfileListResponse!")
            traceback.print_exc()
        break

if __name__ == "__main__":
    asyncio.run(test_counts())
