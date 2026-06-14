from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from uuid import UUID
from src.models.db.resume_instance import UserResumeInstance
from typing import Optional

class ResumeBuilderRepository:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def create_resume_instance(self, user_id: UUID, template_id: str, initial_data: dict) -> UserResumeInstance:
        instance = UserResumeInstance(
            user_id=user_id,
            template_id=template_id,
            resume_data=initial_data,
            status="DRAFT"
        )
        self.db.add(instance)
        await self.db.commit()
        await self.db.refresh(instance)
        return instance

    async def get_resume_by_id_and_user(self, resume_id: UUID, user_id: UUID) -> Optional[UserResumeInstance]:
        stmt = select(UserResumeInstance).where(
            UserResumeInstance.id == resume_id,
            UserResumeInstance.user_id == user_id
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def update_resume_data(self, resume_id: UUID, user_id: UUID, new_data: dict) -> Optional[UserResumeInstance]:
        instance = await self.get_resume_by_id_and_user(resume_id, user_id)
        if instance:
            instance.resume_data = new_data
            await self.db.commit()
            await self.db.refresh(instance)
        return instance