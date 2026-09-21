import asyncio
import json
import os
import sys

# Add backend to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.models.db.interview import Interview
from src.models.db.user import User
from src.models.db.summary_report import SummaryReport
import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://neondb_owner:npg_tJ9qwXkvToh3@ep-wandering-wave-aoqixehm-pooler.c-2.ap-southeast-1.aws.neon.tech:5432/neondb")
engine = create_async_engine(DATABASE_URL)
async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def main():
    async with async_session_maker() as session:
        stmt = (
            sqlalchemy.select(User.university, Interview.difficulty, sqlalchemy.func.count(Interview.id))
            .join(User, User.id == Interview.user_id)
            .where(Interview.status == "completed")
            .group_by(User.university, Interview.difficulty)
        )
        rows = (await session.execute(stmt)).all()
        print("Practice Metrics by College:")
        for r in rows:
            print(r)
            
        stmt2 = (
            sqlalchemy.select(User.university, sqlalchemy.func.count(SummaryReport.id))
            .join(Interview, Interview.id == SummaryReport.interview_id)
            .join(User, User.id == Interview.user_id)
            .group_by(User.university)
        )
        rows2 = (await session.execute(stmt2)).all()
        print("\nWeak Skills (SummaryReport) by College:")
        for r in rows2:
            print(r)

if __name__ == '__main__':
    asyncio.run(main())
