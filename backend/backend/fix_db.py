import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath('.'))

from src.repository.database import async_db
from sqlalchemy import text

async def main():
    try:
        async with async_db.async_session_factory() as session:
            try:
                # Add the missing column
                await session.execute(text("ALTER TABLE job_profile ADD COLUMN IF NOT EXISTS admin_comment TEXT;"))
                print("Column 'admin_comment' added successfully.")
            except Exception as e:
                print(f"Error adding column: {e}")
            
            try:
                # Update alembic_version
                await session.execute(text("UPDATE alembic_version SET version_num = '827560296990';"))
                print("alembic_version updated successfully.")
            except Exception as e:
                print(f"Error updating alembic_version: {e}")

            await session.commit()
    except Exception as e:
        print(f"Connection error: {e}")

asyncio.run(main())
