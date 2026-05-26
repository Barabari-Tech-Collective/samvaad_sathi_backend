from typing import List, Optional
import sqlalchemy
from sqlalchemy import select, func
from src.models.db.job_profile import JobProfile
from src.repository.crud.base import BaseCRUDRepository

class JobProfileCRUDRepository(BaseCRUDRepository):
    # --- feature/roles-page-api methods ---
    async def get_summary(self) -> dict:
        """
        Returns a summary count of job profiles.
        Since status field doesn't exist yet, we return 0 for status-based counts.
        """
        query = select(func.count()).select_from(JobProfile)
        result = await self.async_session.execute(query)
        total_count = result.scalar() or 0
        
        return {
            "totalRoles": total_count,
            "pendingReview": 0,
            "approved": 0,
            "rejected": 0
        }

    async def list_profiles(self, category: Optional[str] = None) -> List[JobProfile]:
        query = select(JobProfile).order_by(JobProfile.created_at.desc())
        
        if category:
            # Filter by job_name as a fallback for category
            query = query.where(JobProfile.job_name.ilike(f"%{category}%"))
            
        result = await self.async_session.execute(query)
        return list(result.scalars().all())

    async def create_profile(self, title: str, description: Optional[str] = None) -> JobProfile:
        new_profile = JobProfile(job_name=title, job_description=description or "")
        self.async_session.add(new_profile)
        await self.async_session.commit()
        await self.async_session.refresh(new_profile)
        return new_profile

    async def delete_profile(self, profile_id: int) -> bool:
        query = select(JobProfile).where(JobProfile.id == profile_id)
        result = await self.async_session.execute(query)
        profile = result.scalar_one_or_none()
        if profile:
            await self.async_session.delete(profile)
            await self.async_session.commit()
            return True
        return False

    async def get_recent_activity(self, limit: int = 5) -> List[dict]:
        """
        Derives recent activity from JobProfile records.
        Returns the latest 'limit' profiles formatted as activity.
        """
        query = select(JobProfile).order_by(JobProfile.created_at.desc()).limit(limit)
        result = await self.async_session.execute(query)
        profiles = result.scalars().all()
        
        activities = []
        for p in profiles:
            activities.append({
                "id": p.id,
                "title": p.job_name,
                "action": "created",
                "message": f"Role '{p.job_name}' was created",
                "createdAt": p.created_at
            })
        return activities

    # --- upstream/master methods ---
    async def create(
        self,
        *,
        job_name: str,
        job_description: str,
        company_name: str | None,
        experience_level: str | None,
        skills: list[str] | None,
        additional_context: str | None,
        created_by: int | None,
    ) -> JobProfile:
        profile = JobProfile(
            job_name=job_name,
            job_description=job_description,
            company_name=company_name,
            experience_level=experience_level,
            skills=skills,
            additional_context=additional_context,
            created_by=created_by,
        )
        self.async_session.add(profile)
        await self.async_session.commit()
        await self.async_session.refresh(profile)
        return profile

    async def list_all(self) -> list[JobProfile]:
        stmt = sqlalchemy.select(JobProfile).order_by(JobProfile.id.desc())
        query = await self.async_session.execute(statement=stmt)
        return list(query.scalars().all())

    async def get_by_id(self, *, job_profile_id: int) -> JobProfile | None:
        stmt = sqlalchemy.select(JobProfile).where(JobProfile.id == job_profile_id)
        query = await self.async_session.execute(statement=stmt)
        return query.scalar_one_or_none()

    async def delete(self, *, job_profile_id: int) -> bool:
        stmt = sqlalchemy.select(JobProfile).where(JobProfile.id == job_profile_id)
        query = await self.async_session.execute(statement=stmt)
        profile = query.scalar_one_or_none()
        if profile is None:
            return False

        await self.async_session.delete(profile)
        await self.async_session.commit()
        return True

    async def create_job_profile_questions(self, questions: list[dict]) -> list[JobProfileQuestion]:
        from src.models.db.job_profile_question import JobProfileQuestion
        db_objs = []
        for q in questions:
            obj = JobProfileQuestion(
                job_profile_id=q["job_profile_id"],
                question_text=q["question_text"],
                level=q["level"],
                difficulty=q["difficulty"],
                question_type=q.get("question_type", "theoretical"),
                is_ai_generated=q.get("is_ai_generated", True)
            )
            self.async_session.add(obj)
            db_objs.append(obj)
        await self.async_session.commit()
        for obj in db_objs:
            await self.async_session.refresh(obj)
        return db_objs

