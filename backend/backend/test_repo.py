import asyncio
import json
from src.repository.database import db
import sqlalchemy
from src.models.db.interview import Interview
from src.models.db.user import User
from src.models.db.question_attempt import QuestionAttempt

async def main():
    async with db.async_session_factory() as session:
        stmt = (
            sqlalchemy.select(QuestionAttempt.analysis_json, Interview.track)
            .join(Interview, Interview.id == QuestionAttempt.interview_id)
            .join(User, User.id == Interview.user_id)
            .where(User.university == 'GDC Begumpet')
        )
        rows = (await session.execute(stmt)).all()
        print("QA Rows:", len(rows))
        for a, t in rows:
            if a: print("Track:", t, "Keys:", a.keys())
            
if __name__ == '__main__':
    asyncio.run(main())
