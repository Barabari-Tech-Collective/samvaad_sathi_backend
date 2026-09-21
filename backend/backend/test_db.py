import asyncio
import json
from src.core.database import async_session_maker
from src.models.db.summary_report import SummaryReport
from src.models.db.interview import Interview
from src.models.db.user import User
import sqlalchemy

async def main():
    async with async_session_maker() as session:
        stmt = sqlalchemy.select(SummaryReport.report_json, Interview.difficulty, Interview.track).join(Interview, Interview.id == SummaryReport.interview_id).join(User, User.id == Interview.user_id).where(User.university == 'GDC Begumpet')
        rows = (await session.execute(stmt)).all()
        print(f"Total rows: {len(rows)}")
        for r, diff, track in rows:
            print(f"Difficulty: {diff}, Track: {track}")
            if r:
                print("Keys:", r.keys())
                score = r.get("overallScoreSummary", {}) or r.get("scoreSummary", {})
                print("Score keys:", score.keys())

if __name__ == '__main__':
    asyncio.run(main())
