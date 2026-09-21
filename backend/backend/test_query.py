import asyncio
from src.core.database import async_session_maker
from src.models.db.interview import Interview
from src.models.db.user import User
from src.models.db.report import Report
from src.models.db.summary_report import SummaryReport
import sqlalchemy

async def main():
    async with async_session_maker() as session:
        stmt = (
            sqlalchemy.select(
                sqlalchemy.func.date(Interview.created_at),
                sqlalchemy.func.avg(sqlalchemy.func.coalesce(Report.overall_score, SummaryReport.overall_score))
            )
            .join(User, User.id == Interview.user_id)
            .outerjoin(Report, Report.interview_id == Interview.id)
            .outerjoin(SummaryReport, SummaryReport.interview_id == Interview.id)
            .where(User.university == "GDC Husaini Alam")
            .group_by(sqlalchemy.func.date(Interview.created_at))
            .order_by(sqlalchemy.func.date(Interview.created_at).asc())
        )
        try:
            rows = list((await session.execute(stmt)).all())
            print(rows)
        except Exception as e:
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
