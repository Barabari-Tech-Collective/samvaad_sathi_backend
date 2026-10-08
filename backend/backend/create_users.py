import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath('.'))

from src.repository.database import async_db
from src.repository.crud.user import UserCRUDRepository
from src.models.schemas.user import UserCreate
from src.models.db.user import User
from sqlalchemy import select

async def main():
    try:
        async with async_db.async_session_factory() as session:
            repo = UserCRUDRepository(session)
            
            # Create admin
            try:
                admin = await repo.create_user(email='admin@barabari.org', password='StrictPassword!@#1', name='Admin User')
                admin.is_admin = True
                print('Created admin')
            except Exception as e:
                print('Admin exists or error:', str(e))
                result = await session.execute(select(User).where(User.email == 'admin@barabari.org'))
                admin = result.scalar_one_or_none()
                if admin:
                    admin.is_admin = True
                    print('Admin updated')
            
            # Create superadmin
            try:
                sa = await repo.create_user(email='superadmin@barabari.org', password='StrictSuperPassword!@#2', name='Super Admin User')
                sa.is_admin = True
                print('Created superadmin')
            except Exception as e:
                print('Superadmin exists or error:', str(e))
                result = await session.execute(select(User).where(User.email == 'superadmin@barabari.org'))
                sa = result.scalar_one_or_none()
                if sa:
                    sa.is_admin = True
                    print('Superadmin updated')
            
            await session.commit()
            print('Done')
    except Exception as e:
        print('Error:', e)

asyncio.run(main())
