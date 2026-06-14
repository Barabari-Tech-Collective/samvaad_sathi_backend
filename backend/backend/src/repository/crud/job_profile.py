from src.models.db import JobProfileQuestion
from typing import List, Optional
import sqlalchemy
from sqlalchemy import select, func
from src.models.db.job_profile import JobProfile
from src.repository.crud.base import BaseCRUDRepository

class JobProfileCRUDRepository(BaseCRUDRepository):
    # --- feature/roles-page-api methods ---
    async def get_summary(self) -> dict:
        """
        Returns a summary count of job profiles based on their status in the database.
        """
        total_stmt = select(func.count()).select_from(JobProfile)
        total_count = (await self.async_session.execute(total_stmt)).scalar() or 0
        
        pending_stmt = select(func.count()).select_from(JobProfile).where(JobProfile.status == "under_review")
        pending_count = (await self.async_session.execute(pending_stmt)).scalar() or 0

        approved_stmt = select(func.count()).select_from(JobProfile).where(JobProfile.status == "approved")
        approved_count = (await self.async_session.execute(approved_stmt)).scalar() or 0

        rejected_stmt = select(func.count()).select_from(JobProfile).where(JobProfile.status == "rejected")
        rejected_count = (await self.async_session.execute(rejected_stmt)).scalar() or 0

        return {
            "totalRoles": total_count,
            "pendingReview": pending_count,
            "approved": approved_count,
            "rejected": rejected_count
        }

    async def list_profiles(
        self,
        *,
        category: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[JobProfile]:
        query = select(JobProfile).order_by(JobProfile.created_at.desc())
        if category:
            query = query.where(JobProfile.category == category)
        if limit is not None:
            query = query.limit(limit)
        result = await self.async_session.execute(query)
        return list(result.scalars().all())

    async def create_profile(
        self,
        job_name: str,
        job_description: Optional[str] = None,
        company_name: Optional[str] = None,
        experience_level: Optional[str] = None,
        skills: Optional[List[str]] = None,
        additional_context: Optional[str] = None,
        category: Optional[str] = None,
        employment_type: Optional[str] = None,
    ) -> JobProfile:
        new_profile = JobProfile(
            job_name=job_name,
            job_description=job_description or "",
            company_name=company_name,
            experience_level=experience_level,
            skills=skills,
            additional_context=additional_context,
            category=category,
            employment_type=employment_type,
        )
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
                is_ai_generated=q.get("is_ai_generated", True),
                keywords=q.get("keywords") or [],
                concepts_covered=q.get("concepts_covered") or [],
                expected_answer=q.get("expected_answer"),
                example_output=q.get("example_output"),
            )
            self.async_session.add(obj)
            db_objs.append(obj)
        await self.async_session.commit()
        for obj in db_objs:
            await self.async_session.refresh(obj)
        return db_objs

    async def get_job_profile_questions(self, job_profile_id: int) -> list[JobProfileQuestion]:
        from src.models.db.job_profile_question import JobProfileQuestion
        stmt = (
            sqlalchemy.select(JobProfileQuestion)
            .where(JobProfileQuestion.job_profile_id == job_profile_id)
            .order_by(JobProfileQuestion.level.asc(), JobProfileQuestion.created_at.asc())
        )
        query = await self.async_session.execute(statement=stmt)
        return list(query.scalars().all())

    async def add_job_profile_question(
        self,
        *,
        job_profile_id: int,
        question_text: str,
        level: int,
        difficulty: str,
        question_type: str = "theoretical",
        is_ai_generated: bool = False,
        keywords: list[str] | None = None,
        concepts_covered: list[str] | None = None,
        expected_answer: str | None = None,
        example_output: str | None = None
    ) -> JobProfileQuestion:
        from src.models.db.job_profile_question import JobProfileQuestion
        obj = JobProfileQuestion(
            job_profile_id=job_profile_id,
            question_text=question_text,
            level=level,
            difficulty=difficulty,
            question_type=question_type,
            is_ai_generated=is_ai_generated,
            keywords=keywords or [],
            concepts_covered=concepts_covered or [],
            expected_answer=expected_answer,
            example_output=example_output
        )
        self.async_session.add(obj)
        await self.async_session.commit()
        await self.async_session.refresh(obj)
        return obj


    async def get_question_by_id(self, question_id: int) -> Optional[JobProfileQuestion]:
        from src.models.db.job_profile_question import JobProfileQuestion
        stmt = sqlalchemy.select(JobProfileQuestion).where(JobProfileQuestion.id == question_id)
        query = await self.async_session.execute(statement=stmt)
        return query.scalar_one_or_none()

    async def update_job_profile_question(self, question: JobProfileQuestion, update_data: dict) -> JobProfileQuestion:
        for key, value in update_data.items():
            if hasattr(question, key) and value is not None:
                setattr(question, key, value)
        self.async_session.add(question)
        await self.async_session.commit()
        await self.async_session.refresh(question)
        return question

    async def delete_job_profile_question(self, question: JobProfileQuestion) -> None:
        await self.async_session.delete(question)
        await self.async_session.commit()

    async def submit_profile(self, *, job_profile_id: int) -> Optional[JobProfile]:
        import datetime
        profile = await self.get_by_id(job_profile_id=job_profile_id)
        if not profile:
            return None
        
        profile.status = "under_review"
        profile.submitted_at = datetime.datetime.now(datetime.timezone.utc)
        self.async_session.add(profile)
        await self.async_session.commit()
        await self.async_session.refresh(profile)
        return profile






