from src.models.db import JobProfileQuestion
from typing import List, Optional
import sqlalchemy
from sqlalchemy import select, func
from src.models.db.job_profile import JobProfile, JobProfileStatus
from src.repository.crud.base import BaseCRUDRepository
from src.services.interview_eligibility import LEVEL_TO_DIFFICULTY


def _difficulty_for_level(level: int) -> str:
    """Derive the difficulty label from the level, never trust a supplied one.

    `job_profile_question` carries both `level` (int) and `difficulty` (str) for
    the same fact, with no constraint tying them together. Everything that
    serves an interview filters on `level`, while facilitator-facing views show
    `difficulty` - so a row where they disagree means a facilitator believes
    they published at one level while students sit the questions at another.
    The manual add-question endpoint validated the level but passed the
    difficulty straight through, which made that reachable.

    Deriving here puts the invariant below every writer, so no caller can create
    a row where the two disagree.
    """
    try:
        return LEVEL_TO_DIFFICULTY[level]
    except KeyError:
        raise ValueError(
            f"Invalid question level {level!r}; expected one of {sorted(LEVEL_TO_DIFFICULTY)}"
        ) from None


class JobProfileCRUDRepository(BaseCRUDRepository):
    async def lock_profile(self, profile_id: int):
        from sqlalchemy.orm import selectinload

        result = await self.async_session.execute(
            select(JobProfile)
            .options(selectinload(JobProfile.questions))
            .where(JobProfile.id == profile_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def require_unpublished(profile):
        import datetime
        import fastapi

        if profile.status == JobProfileStatus.PUBLISHED or isinstance(
            getattr(profile, "published_at", None), datetime.datetime
        ):
            raise fastapi.HTTPException(
                409,
                "Published content is immutable. Create a new draft copy to make changes.",
            )

    async def editable_profile(self, profile_id: int):
        import fastapi

        profile = await self.lock_profile(profile_id)
        if profile is None:
            raise fastapi.HTTPException(404, "Template not found")
        self.require_unpublished(profile)
        return profile

    @staticmethod
    def content_changed(profile):
        current = getattr(profile, "content_revision", 0)
        profile.content_revision = (current if type(current) is int else 0) + 1
        if profile.status == JobProfileStatus.UNDER_REVIEW:
            profile.status = JobProfileStatus.DRAFT
            profile.submitted_at = None
        if getattr(profile, "generation_state", None) in ("queued", "in_progress"):
            profile.generation_state = "stale"

    @staticmethod
    def validate_question(
        profile, *, text: str, level: int, exclude_id: int | None = None
    ):
        import fastapi

        normalized = " ".join(text.split()).casefold()
        if not normalized or len(text) > 10000 or level not in (1, 2, 3, 4):
            raise fastapi.HTTPException(
                422, "Use a nonempty prompt up to 10,000 characters and level 1–4."
            )
        if any(
            q.id != exclude_id
            and q.level == level
            and " ".join(q.question_text.split()).casefold() == normalized
            for q in profile.questions
        ):
            raise fastapi.HTTPException(
                409, "This question already exists at this level."
            )

    async def reserve_generation(self, profile_id: int):
        import uuid
        import fastapi

        profile = await self.editable_profile(profile_id)
        if profile.generation_state in ("queued", "in_progress"):
            raise fastapi.HTTPException(
                409,
                "AI generation is already active. Cancel it before requesting another batch.",
            )
        profile.generation_token = str(uuid.uuid4())
        profile.generation_state = "queued"
        profile.generation_result_count = None
        token, revision = profile.generation_token, profile.content_revision
        await self.async_session.commit()
        return token, revision

    async def finish_generation(self, profile_id: int, token: str, state: str):
        profile = await self.lock_profile(profile_id)
        if (
            profile is not None
            and profile.status != JobProfileStatus.PUBLISHED
            and profile.generation_token == token
            and profile.generation_state in ("queued", "in_progress")
        ):
            profile.generation_state = state
            await self.async_session.commit()

    async def cancel_generation(self, profile_id: int):
        profile = await self.editable_profile(profile_id)
        profile.generation_state = "stale"
        await self.async_session.commit()
        return profile

    async def clone_profile(self, profile_id: int, *, user_id: int, purpose: str):
        import copy
        import fastapi

        profile = await self.lock_profile(profile_id)
        if profile is None:
            raise fastapi.HTTPException(404, "Template not found")
        if profile.interview_mode != purpose:
            raise fastapi.HTTPException(
                409, "A new version must keep its original purpose."
            )
        fields = (
            "job_name",
            "job_description",
            "company_name",
            "experience_level",
            "skills",
            "additional_context",
            "category",
            "employment_type",
            "interview_mode",
            "questions_per_interview",
            "authoring_mode",
        )
        draft = JobProfile(
            **{field: copy.deepcopy(getattr(profile, field)) for field in fields},
            parent_profile_id=profile.id,
            status=JobProfileStatus.DRAFT,
            created_by=user_id,
        )
        self.async_session.add(draft)
        await self.async_session.flush()
        for q in profile.questions:
            fields = (
                "question_text",
                "level",
                "difficulty",
                "question_type",
                "is_ai_generated",
                "keywords",
                "concepts_covered",
                "expected_answer",
                "example_output",
                "audio_url",
            )
            self.async_session.add(
                JobProfileQuestion(
                    job_profile_id=draft.id,
                    **{field: copy.deepcopy(getattr(q, field)) for field in fields},
                )
            )
        await self.async_session.commit()
        return await self.get_by_id(job_profile_id=draft.id)

    async def get_assessment_for_update(self, profile_id: int) -> Optional[JobProfile]:
        result = await self.async_session.execute(
            select(JobProfile).where(JobProfile.id == profile_id).with_for_update()
        )
        profile = result.scalar_one_or_none()
        if profile is not None and profile.interview_mode != "assessment":
            import fastapi

            raise fastapi.HTTPException(
                409,
                "This is a practice template. Start a new assessment or use the reviewed "
                "role-repair process. Existing practice attempts remain practice.",
            )
        return profile

    async def get_practice_for_update(
        self, profile_id: int, *, editable: bool = False
    ) -> Optional[JobProfile]:
        import fastapi

        result = await self.async_session.execute(
            select(JobProfile)
            .where(JobProfile.id == profile_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        profile = result.scalar_one_or_none()
        if profile is not None and profile.interview_mode != "practice":
            raise fastapi.HTTPException(
                409,
                "This is an assessment. Use the assessment creator; existing purpose cannot be changed here.",
            )
        if profile is not None and editable:
            self.require_unpublished(profile)
        return profile

    # --- feature/roles-page-api methods ---
    async def get_summary(self, category: Optional[str] = None) -> dict:
        """
        Returns a summary count of job profiles based on their status in the database.
        Optionally filtered by category / domain.
        """
        total_stmt = select(func.count()).select_from(JobProfile)
        pending_stmt = select(func.count()).select_from(JobProfile).where(JobProfile.status == JobProfileStatus.UNDER_REVIEW)
        approved_stmt = select(func.count()).select_from(JobProfile).where(JobProfile.status == JobProfileStatus.PUBLISHED)
        rejected_stmt = select(func.count()).select_from(JobProfile).where(JobProfile.status == JobProfileStatus.CHANGES_REQUESTED)

        if category and category.strip():
            cat_clean = category.strip().lower()
            total_stmt = total_stmt.where(func.lower(JobProfile.category) == cat_clean)
            pending_stmt = pending_stmt.where(func.lower(JobProfile.category) == cat_clean)
            approved_stmt = approved_stmt.where(func.lower(JobProfile.category) == cat_clean)
            rejected_stmt = rejected_stmt.where(func.lower(JobProfile.category) == cat_clean)

        total_count = (await self.async_session.execute(total_stmt)).scalar() or 0
        pending_count = (await self.async_session.execute(pending_stmt)).scalar() or 0
        approved_count = (await self.async_session.execute(approved_stmt)).scalar() or 0
        rejected_count = (await self.async_session.execute(rejected_stmt)).scalar() or 0

        return {
            "totalRoles": total_count,
            "pendingReview": pending_count,
            "approved": approved_count,
            "rejected": rejected_count,
        }

    async def list_profiles(
        self,
        *,
        category: Optional[str] = None,
        limit: Optional[int] = None,
        is_admin: bool = False,
        interview_mode: str | None = None,
    ) -> List[JobProfile]:
        from sqlalchemy.orm import selectinload

        query = (
            select(JobProfile)
            .options(selectinload(JobProfile.questions))
            .order_by(JobProfile.created_at.desc())
        )

        if interview_mode is not None:
            query = query.where(JobProfile.interview_mode == interview_mode)
        if not is_admin:
            query = query.where(JobProfile.status == JobProfileStatus.PUBLISHED)
        if category and category.strip():
            query = query.where(func.lower(JobProfile.category) == category.strip().lower())
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
        interview_mode: str = "assessment",
        questions_per_interview: int | None = None,
        created_by: Optional[int] = None,
        authoring_mode: str | None = None,
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
            interview_mode=interview_mode,
            questions_per_interview=questions_per_interview,
            authoring_mode=authoring_mode,
            status=JobProfileStatus.DRAFT,
            created_by=created_by,
        )
        self.async_session.add(new_profile)
        await self.async_session.commit()
        await self.async_session.refresh(new_profile)
        return new_profile

    async def update_profile(
        self,
        profile_id: int,
        update_data: dict,
        *,
        expected_interview_mode: str | None = None,
    ) -> Optional[JobProfile]:
        profile = await self.lock_profile(profile_id)
        if not profile:
            return None
        self.require_unpublished(profile)

        if expected_interview_mode is not None and (
            profile.interview_mode != expected_interview_mode
            or update_data.get("interview_mode", expected_interview_mode)
            != expected_interview_mode
        ):
            import fastapi

            raise fastapi.HTTPException(
                status_code=409,
                detail=(
                    "This is not an assessment role. Start a new assessment or use "
                    "the reviewed role-repair process. Existing practice attempts remain practice."
                ),
            )

        if (
            "interview_mode" in update_data
            and update_data["interview_mode"] != profile.interview_mode
        ):
            import fastapi

            raise fastapi.HTTPException(
                409,
                "A template's purpose cannot be changed. Create a separate template.",
            )
        self.content_changed(profile)
        for key, value in update_data.items():
            if hasattr(profile, key):
                setattr(profile, key, value)

        self.async_session.add(profile)
        await self.async_session.commit()
        await self.async_session.refresh(profile)
        return profile

    async def get_recent_activity(self, limit: int = 5, category: Optional[str] = None) -> List[dict]:
        """
        Derives recent activity from JobProfile records.
        Returns the latest 'limit' profiles formatted as activity.
        Optionally filtered by category / domain.
        """
        query = select(JobProfile)
        if category and category.strip():
            query = query.where(func.lower(JobProfile.category) == category.strip().lower())
        query = query.order_by(JobProfile.created_at.desc()).limit(limit)
        result = await self.async_session.execute(query)
        profiles = result.scalars().all()

        activities = []
        for p in profiles:
            activities.append(
                {
                    "id": p.id,
                    "title": p.job_name,
                    "action": "created",
                    "message": f"Role '{p.job_name}' was created",
                    "createdAt": p.created_at,
                }
            )
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
            status=JobProfileStatus.DRAFT,
        )
        self.async_session.add(profile)
        await self.async_session.commit()
        await self.async_session.refresh(profile)
        return profile

    async def list_all(self, *, limit: int = 1000) -> list[JobProfile]:
        from sqlalchemy.orm import selectinload

        stmt = (
            sqlalchemy.select(JobProfile)
            .options(selectinload(JobProfile.questions))
            .order_by(JobProfile.id.desc())
            .limit(limit)
        )
        query = await self.async_session.execute(statement=stmt)
        return list(query.scalars().all())

    async def get_by_id(self, *, job_profile_id: int) -> JobProfile | None:
        from sqlalchemy.orm import selectinload

        stmt = (
            sqlalchemy.select(JobProfile)
            .options(selectinload(JobProfile.questions))
            .where(JobProfile.id == job_profile_id)
        )
        query = await self.async_session.execute(statement=stmt)
        return query.scalar_one_or_none()

    async def update_review_status(
        self,
        profile_id: int,
        status: JobProfileStatus | None,
        admin_comment: str | None,
        expected_revision: int | None = None,
    ) -> JobProfile | None:
        import fastapi
        import datetime

        profile = await self.lock_profile(profile_id)
        if profile is None:
            return None
        self.require_unpublished(profile)
        if (
            expected_revision is not None
            and expected_revision != profile.content_revision
        ):
            raise fastapi.HTTPException(
                409,
                "The draft changed after you opened it. Review the latest version before publishing.",
            )
        if status == JobProfileStatus.PUBLISHED and profile.generation_state in (
            "queued",
            "in_progress",
        ):
            raise fastapi.HTTPException(
                409, "Wait for AI generation or cancel it before publishing."
            )

        if (
            status == JobProfileStatus.PUBLISHED
            and profile.questions_per_interview is not None
        ):
            from src.services.interview_question_plan import unique_questions

            required = profile.questions_per_interview
            if not any(
                len(unique_questions(q for q in profile.questions if q.level == level))
                >= required
                for level in (1, 2, 3, 4)
            ):
                import fastapi

                raise fastapi.HTTPException(
                    409,
                    f"Approve at least {required} unique questions at one level before publishing.",
                )

        if status == JobProfileStatus.PUBLISHED:
            profile.published_at = datetime.datetime.now(datetime.timezone.utc)
            fields = (
                "id",
                "job_name",
                "job_description",
                "company_name",
                "experience_level",
                "skills",
                "additional_context",
                "category",
                "employment_type",
                "interview_mode",
                "questions_per_interview",
                "authoring_mode",
                "content_revision",
                "parent_profile_id",
            )
            qfields = (
                "id",
                "question_text",
                "level",
                "difficulty",
                "question_type",
                "is_ai_generated",
                "keywords",
                "concepts_covered",
                "expected_answer",
                "example_output",
                "audio_url",
            )
            profile.publication_snapshot = {
                "version": 1,
                "publishedAt": profile.published_at.isoformat(),
                "profile": {field: getattr(profile, field) for field in fields},
                "questions": [
                    {field: getattr(q, field) for field in qfields}
                    for q in sorted(profile.questions, key=lambda q: q.id)
                ],
            }
        # Both fields are optional so this stays a true partial update: omitting one must
        # leave the stored value alone. Without the admin_comment guard, publishing a role
        # without re-sending the comment wipes the concerns an admin recorded earlier.
        # Passing an empty string is still how a caller clears it.
        if status is not None:
            profile.status = status
        if admin_comment is not None:
            profile.admin_comment = admin_comment
        await self.async_session.commit()
        await self.async_session.refresh(profile)
        return profile

    async def delete(self, *, job_profile_id: int) -> bool:
        profile = await self.lock_profile(job_profile_id)
        if profile is None:
            return False
        self.require_unpublished(profile)
        await self.async_session.delete(profile)
        await self.async_session.commit()
        return True

    async def create_job_profile_questions(
        self,
        questions: list[dict],
        *,
        expected_revision: int | None = None,
        generation_token: str | None = None,
    ) -> list[JobProfileQuestion]:
        from src.models.db.job_profile_question import JobProfileQuestion

        import fastapi

        ids = sorted({q["job_profile_id"] for q in questions})
        parents = []
        for profile_id in ids:
            profile = await self.editable_profile(profile_id)
            if generation_token is not None and (
                profile.generation_token != generation_token
                or profile.generation_state not in ("queued", "in_progress")
                or profile.content_revision != expected_revision
            ):
                raise fastapi.HTTPException(
                    409,
                    "AI output is stale; the draft was changed or the request was cancelled.",
                )
            parents.append(profile)
        known = {
            (profile.id, q.level, " ".join(q.question_text.split()).casefold())
            for profile in parents
            for q in profile.questions
        }
        for item in questions:
            text = item.get("question_text", "")
            key = (
                item["job_profile_id"],
                item["level"],
                " ".join(text.split()).casefold(),
            )
            if not text.strip() or key in known:
                raise fastapi.HTTPException(
                    409,
                    "AI returned blank or duplicate questions. Existing draft content was preserved; try another request.",
                )
            known.add(key)
        for profile in parents:
            self.content_changed(profile)
            if generation_token is not None:
                profile.generation_state = "complete"
                profile.generation_result_count = sum(
                    1 for q in questions if q["job_profile_id"] == profile.id
                )
        db_objs = []
        for q in questions:
            obj = JobProfileQuestion(
                job_profile_id=q["job_profile_id"],
                question_text=q["question_text"],
                level=q["level"],
                difficulty=_difficulty_for_level(q["level"]),
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

    async def get_job_profile_questions(
        self, job_profile_id: int
    ) -> list[JobProfileQuestion]:
        from src.models.db.job_profile_question import JobProfileQuestion

        stmt = (
            sqlalchemy.select(JobProfileQuestion)
            .where(JobProfileQuestion.job_profile_id == job_profile_id)
            .order_by(
                JobProfileQuestion.level.asc(), JobProfileQuestion.created_at.asc()
            )
        )
        query = await self.async_session.execute(statement=stmt)
        return list(query.scalars().all())

    async def add_job_profile_question(
        self,
        *,
        job_profile_id: int,
        question_text: str,
        level: int,
        difficulty: str | None = None,
        question_type: str = "theoretical",
        is_ai_generated: bool = False,
        keywords: list[str] | None = None,
        concepts_covered: list[str] | None = None,
        expected_answer: str | None = None,
        example_output: str | None = None,
    ) -> JobProfileQuestion:
        """`difficulty` is accepted for call compatibility but always derived.

        See _difficulty_for_level: the two columns describe the same thing, and
        a caller-supplied difficulty could disagree with the level everything
        else filters on.
        """
        from src.models.db.job_profile_question import JobProfileQuestion

        profile = await self.editable_profile(job_profile_id)
        self.validate_question(profile, text=question_text, level=level)
        self.content_changed(profile)
        obj = JobProfileQuestion(
            job_profile_id=job_profile_id,
            question_text=question_text,
            level=level,
            difficulty=_difficulty_for_level(level),
            question_type=question_type,
            is_ai_generated=is_ai_generated,
            keywords=keywords or [],
            concepts_covered=concepts_covered or [],
            expected_answer=expected_answer,
            example_output=example_output,
        )
        self.async_session.add(obj)
        await self.async_session.commit()
        await self.async_session.refresh(obj)
        return obj

    async def get_question_by_id(
        self, question_id: int
    ) -> Optional[JobProfileQuestion]:
        from src.models.db.job_profile_question import JobProfileQuestion

        stmt = sqlalchemy.select(JobProfileQuestion).where(
            JobProfileQuestion.id == question_id
        )
        query = await self.async_session.execute(statement=stmt)
        return query.scalar_one_or_none()

    async def update_job_profile_question(
        self,
        question: JobProfileQuestion,
        update_data: dict,
        *,
        expected_revision: int | None = None,
    ) -> JobProfileQuestion:
        profile = await self.editable_profile(question.job_profile_id)
        if (
            expected_revision is not None
            and profile.content_revision != expected_revision
        ):
            import fastapi

            raise fastapi.HTTPException(
                409, "AI output is stale because the draft changed."
            )
        level = update_data.get("level", question.level)
        self.validate_question(
            profile,
            text=update_data.get("question_text", question.question_text),
            level=level,
            exclude_id=question.id,
        )
        update_data["difficulty"] = _difficulty_for_level(level)
        self.content_changed(profile)
        for key, value in update_data.items():
            if hasattr(question, key):
                setattr(question, key, value)
        self.async_session.add(question)
        await self.async_session.commit()
        await self.async_session.refresh(question)
        return question

    async def delete_job_profile_question(self, question: JobProfileQuestion) -> None:
        profile = await self.editable_profile(question.job_profile_id)
        self.content_changed(profile)
        await self.async_session.delete(question)
        await self.async_session.commit()

    async def delete_all_job_profile_questions(self, job_profile_id: int) -> int:
        """Delete every question for a profile. Returns the number deleted."""
        from src.models.db.job_profile_question import JobProfileQuestion as JPQ

        profile = await self.editable_profile(job_profile_id)
        self.content_changed(profile)
        stmt = sqlalchemy.delete(JPQ).where(JPQ.job_profile_id == job_profile_id)
        result = await self.async_session.execute(stmt)
        await self.async_session.commit()
        return result.rowcount

    async def submit_profile(self, *, job_profile_id: int) -> Optional[JobProfile]:
        import datetime

        profile = await self.editable_profile(job_profile_id)
        profile.status = JobProfileStatus.UNDER_REVIEW
        profile.submitted_at = datetime.datetime.now(datetime.timezone.utc)
        self.async_session.add(profile)
        await self.async_session.commit()
        await self.async_session.refresh(profile)
        return profile
