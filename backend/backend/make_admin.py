import asyncio
import sys
sys.path.append('.')
from src.api.dependencies.session import get_async_session
import sqlalchemy
from src.models.db.user import User

async def make_admin():
    async for session in get_async_session():
        # Let's find all users that have admin in their email and print them
        stmt = sqlalchemy.select(User).where(User.email.ilike('admin@%'))
        result = await session.execute(stmt)
        users = result.scalars().all()
        for u in users:
            print(f"Setting {u.email} to admin...")
            u.is_admin = True
        
        await session.commit()
        print("Done.")
        break

if __name__ == "__main__":
    asyncio.run(make_admin())
