import asyncio
import sys
sys.path.append('.')
from src.api.dependencies.session import get_async_session
from sqlalchemy import text

async def alter_table():
    async for session in get_async_session():
        try:
            await session.execute(text("ALTER TABLE job_profile ADD COLUMN admin_comment TEXT;"))
            await session.commit()
            print("Successfully added admin_comment column!")
        except Exception as e:
            if "already exists" in str(e).lower():
                print("Column already exists.")
            else:
                print(f"Error: {e}")
        break

if __name__ == "__main__":
    asyncio.run(alter_table())
