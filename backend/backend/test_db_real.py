import asyncio
from src.api.dependencies.session import get_async_session_maker
from src.models.db.summary_report import SummaryReport
from src.models.db.interview import Interview
from src.models.db.user import User
import sqlalchemy
import json

async def main():
    async_session = get_async_session_maker()
    async with async_session() as session:
        stmt = sqlalchemy.select(SummaryReport.report_json, Interview.difficulty, Interview.track).join(Interview, Interview.id == SummaryReport.interview_id).join(User, User.id == Interview.user_id).where(User.university == 'GDC Begumpet')
        rows = (await session.execute(stmt)).all()
        print(f"Total rows: {len(rows)}")
        for r, diff, track in rows:
            print(f"Difficulty: {diff}, Track: {track}")
            if r:
                print(json.dumps(r, indent=2))
                break

if __name__ == '__main__':
    asyncio.run(main())
