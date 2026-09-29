import asyncio
import sys
sys.path.append('.')
from src.api.dependencies.session import get_async_session
from src.repository.crud.job_profile import JobProfileCRUDRepository
from src.models.schemas.job_profile import JobProfileResponse

async def test_api():
    async for session in get_async_session():
        repo = JobProfileCRUDRepository(session)
        profiles = await repo.list_all(limit=5)
        for p in profiles:
            print(f"Profile: {p.title} (ID: {p.id})")
            print(f"  Easy DB property: {p.easy_questions}")
            print(f"  Medium DB property: {p.medium_questions}")
            print(f"  Hard DB property: {p.hard_questions}")
            print(f"  Expert DB property: {p.expert_questions}")
            
            # Serialize
            schema = JobProfileResponse.model_validate(p)
            print(f"  Serialized Easy: {schema.easy_questions}")
            print(f"  Serialized Medium: {schema.medium_questions}")
            print(f"  Serialized Hard: {schema.hard_questions}")
            print(f"  Serialized Advanced: {schema.advanced_questions}")
            print("-" * 30)
        break

if __name__ == "__main__":
    asyncio.run(test_api())
