import asyncio
from src.core.database import async_session_maker
from src.models.db.interview import Interview
from src.models.db.user import User
from src.models.db.question_attempt import QuestionAttempt
import sqlalchemy
from collections import defaultdict

async def main():
    async with async_session_maker() as session:
        stmt = (
            sqlalchemy.select(QuestionAttempt.analysis_json, Interview.track)
            .join(Interview, Interview.id == QuestionAttempt.interview_id)
            .join(User, User.id == Interview.user_id)
            .where(User.university == 'GDC Begumpet')
        )
        rows = list((await session.execute(stmt)).all())
        counts = defaultdict(int)
        for analysis_json, role in rows:
            analysis = analysis_json or {}
            communication = analysis.get("communication") if isinstance(analysis, dict) else {}
            domain = analysis.get("domain") if isinstance(analysis, dict) else {}
            weaknesses = []
            if isinstance(communication, dict):
                weaknesses.extend(communication.get("improvements") or [])
                weaknesses.extend(communication.get("recommendations") or [])
            if isinstance(domain, dict):
                weaknesses.extend(domain.get("improvements") or [])
            for weakness in weaknesses:
                if isinstance(weakness, str) and weakness.strip():
                    counts[(role or "unknown", weakness.strip().lower())] += 1
        print("Rows:", len(rows))
        print("Counts:", dict(counts))

if __name__ == '__main__':
    asyncio.run(main())
