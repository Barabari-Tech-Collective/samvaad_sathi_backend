import asyncio
from src.repository.database import async_db
from src.services.analytics import AnalyticsService
import json

async def main():
    session = async_db.get_session()
    service = AnalyticsService(session)
    print("Running for student 557...")
    try:
        res = await service.get_student_level_analytics(user_id=557)
        # Convert to dict and print some keys to ensure we got data
        print("Success!")
        print("Performance:", res.get("performance", {}).keys())
        print("Latest Score:", res.get("performance", {}).get("latest_score"))
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        await session.close()

if __name__ == "__main__":
    asyncio.run(main())
