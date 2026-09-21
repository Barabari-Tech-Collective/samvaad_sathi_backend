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
        # Check practice metrics logic
        print("=== Practice Metrics Check ===")
        stmt = (
            sqlalchemy.select(Interview.id, Interview.difficulty, Interview.status)
            .join(User, User.id == Interview.user_id)
            .where(User.university == 'GDC Begumpet')
        )
        rows = (await session.execute(stmt)).all()
        print(f"Total Interviews for GDC Begumpet: {len(rows)}")
        for i_id, diff, status in rows:
            print(f"ID: {i_id} | Status: {status} | Difficulty: {diff}")
            
        print("\n=== Weak Skills Check ===")
        stmt2 = (
            sqlalchemy.select(SummaryReport.report_json, Interview.track)
            .join(Interview, Interview.id == SummaryReport.interview_id)
            .join(User, User.id == Interview.user_id)
            .where(User.university == 'GDC Begumpet')
            .limit(5)
        )
        rows2 = (await session.execute(stmt2)).all()
        print(f"Total SummaryReports for GDC Begumpet: {len(rows2)}")
        for r_json, track in rows2:
            print(f"Track: {track}")
            if r_json:
                print("JSON Keys:", r_json.keys())
                score = r_json.get("overallScoreSummary") or r_json.get("scoreSummary") or {}
                if score:
                    print("Score keys:", score.keys())
                    print("Knowledge:", score.get("knowledgeCompetence", {}).keys())
                    print("Speech:", (score.get("speechStructure") or score.get("speechAndStructure") or {}).keys())
            print("---")

if __name__ == '__main__':
    asyncio.run(main())
