from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.ai_resume_analysis import AIResumeAnalysis


class AIResumeAnalysisCRUDRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_analysis(
        self,
        analysis_id: str,
        user_id: int,
        target_role: str,
        experience_level: str,
        job_description: str,
        extracted_resume_text: str,
        analysis_result: dict,
    ):
        """
        Save ATS analysis into database
        """

        analysis = AIResumeAnalysis(
            analysis_id=analysis_id,
            user_id=user_id,
            target_role=target_role,
            experience_level=experience_level,
            job_description=job_description,
            extracted_resume_text=extracted_resume_text,
            analysis_result=analysis_result,
        )

        self.session.add(analysis)

        await self.session.commit()

        await self.session.refresh(analysis)

        return analysis

    async def get_by_analysis_id(
        self,
        analysis_id: str,
    ):
        """
        Fetch ATS analysis by analysis_id
        """

        query = select(AIResumeAnalysis).where(
            AIResumeAnalysis.analysis_id == analysis_id
        )

        result = await self.session.execute(query)

        return result.scalar_one_or_none()