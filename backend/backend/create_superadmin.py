import asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
import bcrypt

from src.config.manager import settings
from src.models.db.user import User

async def create_super_admin():
    from src.repository.database import async_db
    async_session = async_db.async_session_factory
    
    email = "superadmin@barabari.org"
    password = "Password@1234"
    name = "Super Admin"

    # Hash the password using bcrypt (matching local authentication scheme)
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

    async with async_session() as session:
        # Check if user already exists
        from sqlalchemy.future import select
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalars().first()

        if user:
            print(f"User {email} already exists. Upgrading to Super Admin...")
            user.is_super_admin = True
            user.is_admin = True
            if not user.password_hash or user.password_hash == "":
                user.password_hash = hashed_password
        else:
            print(f"Creating new Super Admin account: {email}")
            user = User(
                email=email,
                name=name,
                password_hash=hashed_password,
                is_admin=True,
                is_super_admin=True,
                is_onboarded=True
            )
            session.add(user)
        
        await session.commit()
        print("-------------------------------------------------")
        print("SUCCESS! Super Admin account created.")
        print(f"Email: {email}")
        print(f"Password: {password}")
        print("-------------------------------------------------")

if __name__ == "__main__":
    asyncio.run(create_super_admin())
