import sqlalchemy

from src.models.db.user import TargetPositionEnum

from src.models.db.user import User
from src.repository.crud.base import BaseCRUDRepository
from src.securities.hashing.password import pwd_generator
from src.utilities.exceptions.database import EntityAlreadyExists, EntityDoesNotExist
from src.utilities.exceptions.password import PasswordDoesNotMatch


class UserCRUDRepository(BaseCRUDRepository):
    async def get_user_by_id(self, *, user_id: int) -> User:
        stmt = sqlalchemy.select(User).where(User.id == user_id)
        query = await self.async_session.execute(statement=stmt)
        user = query.scalar()
        if not user:
            raise EntityDoesNotExist("User does not exist!")
        return user  # type: ignore

    async def create_user(self, *, email: str, password: str, name: str) -> User:
        stmt = sqlalchemy.select(User).where(User.email == email)
        query = await self.async_session.execute(statement=stmt)
        if query.scalar():
            raise EntityAlreadyExists("User with this email already exists!")

        new_user = User(email=email, name=name, password_hash="")
        # Use a stable empty salt for User since the model does not store per-user salt
        new_user.password_hash = pwd_generator.generate_hashed_password(hash_salt="", new_password=password)

        self.async_session.add(new_user)
        await self.async_session.commit()
        await self.async_session.refresh(new_user)
        return new_user

    async def get_user_by_email(self, *, email: str) -> User:
        stmt = sqlalchemy.select(User).where(User.email == email)
        query = await self.async_session.execute(statement=stmt)
        user = query.scalar()
        if not user:
            raise EntityDoesNotExist("User with this email does not exist!")
        return user  # type: ignore

    async def verify_password(self, *, email: str, password: str) -> User:
        user = await self.get_user_by_email(email=email)
        if not pwd_generator.is_password_authenticated(hash_salt="", password=password, hashed_password=user.password_hash):
            raise PasswordDoesNotMatch("Password does not match!")
        return user

    async def update_resume_data(
        self,
        *,
        user_id: int,
        resume_text: str | None,
        years_experience: float | None,
        skills: list[str] | None,
        degree: str | None = None,
        university: str | None = None,
        company: str | None = None,
        original_resume_s3_key: str | None = None,
    ) -> User:
        stmt = sqlalchemy.select(User).where(User.id == user_id)
        query = await self.async_session.execute(statement=stmt)
        user: User | None = query.scalar()  # type: ignore
        if not user:
            raise EntityDoesNotExist("User does not exist!")

        user.resume_text = resume_text
        if years_experience is not None:
            try:
                user.years_experience = float(years_experience)
            except Exception:
                user.years_experience = None
        # Store skills as JSON; keep shape flexible
        user.skills = {"items": skills or []}

        # Opportunistically persist simple profile fields if provided
        if degree is not None and degree.strip():
            user.degree = degree.strip()
        if university is not None and university.strip():
            user.university = university.strip()
        if company is not None and company.strip():
            user.company = company.strip()
        if original_resume_s3_key is not None:
            user.original_resume_s3_key = original_resume_s3_key

        await self.async_session.commit()
        await self.async_session.refresh(user)
        return user

    async def update_original_resume_s3_key(self, *, user_id: int, s3_key: str) -> User:
        stmt = sqlalchemy.select(User).where(User.id == user_id)
        query = await self.async_session.execute(statement=stmt)
        user: User | None = query.scalar()  # type: ignore
        if not user:
            raise EntityDoesNotExist("User does not exist!")

        user.original_resume_s3_key = s3_key
        await self.async_session.commit()
        await self.async_session.refresh(user)
        return user

    async def update_only_resume_text(self, *, user_id: int, resume_text: str) -> User:
        stmt = sqlalchemy.select(User).where(User.id == user_id)
        query = await self.async_session.execute(statement=stmt)
        user: User | None = query.scalar()  # type: ignore
        if not user:
            raise EntityDoesNotExist("User does not exist!")

        user.resume_text = resume_text
        await self.async_session.commit()
        await self.async_session.refresh(user)
        return user

    async def set_student_id_if_unset(self, *, user_id: int, student_id: str) -> User:
        """Only sets it if empty, so a duplicate link click can't clobber an existing link."""
        stmt = sqlalchemy.select(User).where(User.id == user_id)
        query = await self.async_session.execute(statement=stmt)
        user: User | None = query.scalar()  # type: ignore
        if not user:
            raise EntityDoesNotExist("User does not exist!")

        if not user.student_id:
            user.student_id = student_id
            await self.async_session.commit()
            await self.async_session.refresh(user)
        return user  # type: ignore

    async def set_onboarded(self, *, user_id: int, value: bool = True) -> User:
        stmt = sqlalchemy.select(User).where(User.id == user_id)
        query = await self.async_session.execute(statement=stmt)
        user: User | None = query.scalar()  # type: ignore
        if not user:
            raise EntityDoesNotExist("User does not exist!")
        # If column exists, set; otherwise ignore to be backward compatible
        if hasattr(user, "is_onboarded"):
            user.is_onboarded = bool(value)
            await self.async_session.commit()
            await self.async_session.refresh(user)
        return user  # type: ignore

    # ------------------------------------------------------------------
    # Profile update
    # ------------------------------------------------------------------
    async def update_user_profile(
        self,
        *,
        user_id: int,
        degree: str | None = None,
        university: str | None = None,
        target_position: str | None = None,
        years_experience: float | None = None,
    ) -> User:
        """Update the profile attributes for a given user.

        Only non-None parameters will be updated. If all are None, the call is a no-op.
        """
        # Fetch user first
        stmt = sqlalchemy.select(User).where(User.id == user_id)
        query = await self.async_session.execute(stmt)
        user: User | None = query.scalar()  # type: ignore
        if not user:
            raise EntityDoesNotExist("User does not exist!")

        # Apply patch fields
        if degree is not None:
            user.degree = degree.strip() if degree else None
        if university is not None:
            user.university = university.strip() if university else None
        if target_position is not None:
            user.target_position = target_position.strip() if target_position else None
        if years_experience is not None:
            try:
                user.years_experience = float(years_experience)
            except Exception:
                user.years_experience = None

        await self.async_session.commit()
        await self.async_session.refresh(user)
        return user

    async def delete_user(self, *, user_id: int) -> None:
        """Deletes a user and ensures their S3 resumes are cleaned up to prevent orphaned objects."""
        from src.models.db.user_resume import UserResume
        from src.services.s3_service import BUCKET_NAME, s3_client
        import asyncio
        import logging
        
        logger = logging.getLogger(__name__)

        # 1. Fetch user's resumes to delete them from S3
        stmt = sqlalchemy.select(UserResume).where(UserResume.user_id == user_id)
        result = await self.async_session.execute(stmt)
        user_resumes = result.scalars().all()
        
        # 2. Cleanup S3 path to prevent orphaned bucket objects
        loop = asyncio.get_running_loop()
        for resume in user_resumes:
            if resume.s3_key and s3_client and BUCKET_NAME:
                try:
                    await loop.run_in_executor(
                        None,
                        lambda k: s3_client.delete_object(Bucket=BUCKET_NAME, Key=k),
                        resume.s3_key
                    )
                    logger.info(f"Cleaned up S3 object {resume.s3_key} for deleted user {user_id}")
                except Exception as e:
                    logger.error(f"Failed to clean up S3 object {resume.s3_key} for deleted user {user_id}: {e}")
        
        # 3. Now delete the user (DB CASCADE will automatically delete the UserResume rows)
        del_stmt = sqlalchemy.delete(User).where(User.id == user_id)
        await self.async_session.execute(del_stmt)
        await self.async_session.commit()
