import asyncio
import secrets
from src.repository.database import async_db
from src.repository.crud.user import UserCRUDRepository
from src.utilities.exceptions.database import EntityDoesNotExist

async def main():
    print("Connecting to database...")
    async with async_db.async_session_factory() as session:
        repo = UserCRUDRepository(session)
        try:
            user = await repo.get_user_by_email(email="admin@barabari.org")
            print(f"User already exists: {user.email}. Setting admin status...")
            await repo.set_admin_status(email="admin@barabari.org", is_admin=True)
            print("Done!")
        except EntityDoesNotExist:
            print("User not found, creating new admin account...")
            user = await repo.create_user(
                email="admin@barabari.org", 
                password=secrets.token_urlsafe(32), 
                name="Central Admin"
            )
            # Note: This is an out-of-band permission grant that lives only in Samvaad Saathi's database.
            # If central admin rights are revoked in the auth service, this local row will still
            # grant admin access unless manually removed.
            await repo.set_admin_status(email="admin@barabari.org", is_admin=True)
            print("Successfully created central admin account in Samvaad Saathi database!")

if __name__ == "__main__":
    asyncio.run(main())
