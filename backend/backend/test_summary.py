import asyncio
from src.core.database import async_session_maker
from src.models.db.summary_report import SummaryReport
from src.models.db.interview import Interview
from src.models.db.user import User
import sqlalchemy
import json

async def main():
    async with async_session_maker() as session:
        stmt = sqlalchemy.select(SummaryReport.report_json).join(Interview, Interview.id == SummaryReport.interview_id).join(User, User.id == Interview.user_id).where(User.university == 'GDC Begumpet').limit(1)
        row = (await session.execute(stmt)).scalar()
        print(json.dumps(row, indent=2))

if __name__ == '__main__':
    asyncio.run(main())
