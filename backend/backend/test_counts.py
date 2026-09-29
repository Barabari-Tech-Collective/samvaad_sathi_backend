import asyncio
import sys
import traceback
sys.path.append('.')
from src.api.dependencies.session import get_async_session
from src.repository.crud.job_profile import JobProfileCRUDRepository
from src.models.schemas.job_profile import JobProfileResponse

async def test_counts():
    async for session in get_async_session():
        repo = JobProfileCRUDRepository(session)
        profiles = await repo.list_profiles(limit=5)
        for p in profiles:
            print(f"Profile ID: {p.id}, Name: {p.job_name}")
            try:
                # directly access property
                print(f"  Direct access - Easy: {p.easy_questions}, Total: {p.total_questions}, Questions loaded: {len(p.questions) if p.questions else 0}")
                
                # validate via pydantic
                pydantic_model = JobProfileResponse.model_validate(p)
                print(f"  Pydantic access - Easy: {pydantic_model.easy_questions}, Total: {pydantic_model.totalQuestions}")
            except Exception as e:
                print(f"  Exception: {e}")
                traceback.print_exc()
        break

if __name__ == "__main__":
    asyncio.run(test_counts())
