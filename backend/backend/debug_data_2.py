import asyncio
import json
import os
import sys

# Add backend to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.models.db.summary_report import SummaryReport
from src.models.db.interview import Interview
from src.models.db.user import User
import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://neondb_owner:npg_tJ9qwXkvToh3@ep-wandering-wave-aoqixehm-pooler.c-2.ap-southeast-1.aws.neon.tech:5432/neondb")
engine = create_async_engine(DATABASE_URL)
async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def main():
    async with async_session_maker() as session:
        stmt2 = (
            sqlalchemy.select(SummaryReport.report_json)
            .join(Interview, Interview.id == SummaryReport.interview_id)
            .join(User, User.id == Interview.user_id)
            .where(User.university == 'GDC Begumpet')
            .limit(1)
        )
        r_json = (await session.execute(stmt2)).scalar()
        if r_json:
            print(json.dumps(r_json, indent=2))

if __name__ == '__main__':
    asyncio.run(main())
