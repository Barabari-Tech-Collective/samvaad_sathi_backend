import asyncio
import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()

from src.models.db.interview import Interview
from src.models.db.question_attempt import QuestionAttempt

from src.models.db.summary_report import SummaryReport

async def main():
    db_url = f"postgresql+asyncpg://{os.environ['POSTGRES_USERNAME']}:{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
    
    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        stmt = (
            sqlalchemy.select(QuestionAttempt.id, QuestionAttempt.interview_id, QuestionAttempt.analysis_json, QuestionAttempt.created_at)
            .join(Interview, Interview.id == QuestionAttempt.interview_id)
            .where(Interview.user_id == 14)
            .where(QuestionAttempt.analysis_json.is_not(None))
            .order_by(QuestionAttempt.created_at.desc())
        )
        rows = list((await session.execute(stmt)).all())
        for r in rows:
            qa_id, iv_id, a_json, created_at = r
            if a_json and 'domain' in a_json:
                print(qa_id, iv_id, a_json['domain'].keys())
                break

if __name__ == "__main__":
    asyncio.run(main())
