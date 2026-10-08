import asyncio
from sqlalchemy.future import select
from src.repository.database import async_db
from src.models.db.user import User
from src.api.dependencies.admin import is_super_admin_user
from src.config.manager import settings

async def test_admin():
    async_session = async_db.async_session_factory
    
    print("settings.SUPER_ADMIN_EMAILS:", getattr(settings, "SUPER_ADMIN_EMAILS", "NOT FOUND!"))
    
    async with async_session() as session:
        result = await session.execute(select(User).where(User.email == 'admin@barabari.org'))
        user = result.scalars().first()
        
        if not user:
            print("User admin@barabari.org not found in DB!")
            return
            
        print("User found:", user.email)
        print("user.is_super_admin in DB:", getattr(user, "is_super_admin", False))
        print("is_super_admin_user(user) evaluation:", is_super_admin_user(user))

if __name__ == "__main__":
    asyncio.run(test_admin())
