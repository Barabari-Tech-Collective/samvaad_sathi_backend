import sqlalchemy

from src.models.db.user import TargetPositionEnum

from src.models.db.user import User
from src.repository.crud.base import BaseCRUDRepository
from src.securities.hashing.password import pwd_generator
from src.utilities.exceptions.database import EntityAlreadyExists, EntityDoesNotExist
from src.utilities.exceptions.password import PasswordDoesNotMatch


class UserCRUDRepository(BaseCRUDRepository):
    async def get_user_by_central_identity(self, central_id: str) -> User:
        result = await self.async_session.execute(sqlalchemy.select(User).where(User.central_identity_id == central_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise EntityDoesNotExist("Central identity is not bound")
        return user

    async def bind_central_identity(self, user: User, central_id: str) -> User:
        from sqlalchemy.exc import IntegrityError
        if user.central_identity_id not in (None, central_id):
            raise EntityAlreadyExists("This local account is bound to another central identity")
        if user.central_identity_id == central_id:
            return user
        try:
            result = await self.async_session.execute(sqlalchemy.update(User).where(User.id == user.id, User.central_identity_id.is_(None)).values(central_identity_id=central_id))
            if result.rowcount != 1:
                raise EntityAlreadyExists("Identity binding changed; sign in again")
            await self.async_session.commit()
            await self.async_session.refresh(user)
            return user
        except IntegrityError:
            await self.async_session.rollback()
            raise EntityAlreadyExists("Central identity is already bound")

    async def get_user_by_id(self, *, user_id: int) -> User:
        stmt = sqlalchemy.select(User).where(User.id == user_id)
        query = await self.async_session.execute(statement=stmt)
        user = query.scalar()
        if not user:
            raise EntityDoesNotExist("User does not exist!")
        return user  # type: ignore

    async def create_user(self, *, email: str, password: str, name: str) -> User:
        clean_email = (email or "").strip().lower()
        stmt = sqlalchemy.select(User).where(sqlalchemy.func.lower(User.email) == clean_email)
        query = await self.async_session.execute(statement=stmt)
        if query.scalar():
            raise EntityAlreadyExists("User with this email already exists!")

        new_user = User(email=clean_email, name=(name or "").strip(), password_hash="")
        # Use a stable empty salt for User since the model does not store per-user salt
        new_user.password_hash = pwd_generator.generate_hashed_password(hash_salt="", new_password=password)

        self.async_session.add(new_user)
        await self.async_session.commit()
        await self.async_session.refresh(new_user)
        return new_user

    async def get_user_by_email(self, *, email: str) -> User:
        raw_email = (email or "").strip()
        clean_email = raw_email.lower()

        # 1. First attempt exact match
        stmt_exact = sqlalchemy.select(User).where(User.email == raw_email)
        query_exact = await self.async_session.execute(statement=stmt_exact)
        try:
            user = query_exact.scalar_one_or_none() if hasattr(query_exact, "scalar_one_or_none") else None
            if isinstance(user, User):
                return user
            if hasattr(query_exact, "scalar"):
                user_s = query_exact.scalar()
                if isinstance(user_s, User):
                    return user_s
        except Exception:
            pass

        # 2. Case-insensitive fallback with deterministic ordering
        stmt_ci = (
            sqlalchemy.select(User)
            .where(sqlalchemy.func.lower(User.email) == clean_email)
            .order_by(User.id.asc())
        )
        query_ci = await self.async_session.execute(statement=stmt_ci)
        try:
            if hasattr(query_ci, "scalars"):
                s_obj = query_ci.scalars()
                if hasattr(s_obj, "all"):
                    matched_users = [u for u in s_obj.all() if isinstance(u, User)]
                    if matched_users:
                        return matched_users[0]
            if hasattr(query_ci, "scalar_one_or_none"):
                user_ci = query_ci.scalar_one_or_none()
                if isinstance(user_ci, User):
                    return user_ci
            if hasattr(query_ci, "scalar"):
                user_ci_s = query_ci.scalar()
                if isinstance(user_ci_s, User):
                    return user_ci_s
        except Exception:
            pass

        raise EntityDoesNotExist("User with this email does not exist!")

    async def verify_password(self, *, email: str, password: str) -> User:
        user = await self.get_user_by_email(email=email)
        if not user.password_hash:
            raise PasswordDoesNotMatch("This account is configured for Central SSO login.")
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

    async def sync_central_identity(
        self,
        *,
        user_id: int,
        central_user_id: str,
        name: str | None = None,
        degree: str | None = None,
        university: str | None = None,
    ) -> User:
        """Link the stable central ID and refresh only fields owned by the registry."""
        user = await self.get_user_by_id(user_id=user_id)
        if user.student_id and user.student_id != central_user_id:
            raise EntityAlreadyExists("This Samvaad account is linked to another central account")
        user.student_id = central_user_id
        if name:
            user.name = name
        if degree:
            user.degree = degree
        if university:
            user.university = university
        await self.async_session.commit()
        await self.async_session.refresh(user)
        return user

    async def set_onboarded(self, *, user_id: int, value: bool = True) -> User:
        stmt = sqlalchemy.select(User).where(User.id == user_id)
        query = await self.async_session.execute(statement=stmt)
        user: User | None = query.scalar()  # type: ignore
        if not user:
            raise EntityDoesNotExist("User does not exist!")
        # If column exists, set; otherwise ignore to be backward compatible
        if hasattr(user, "is_onboarded"):
            user.is_onboarded = value
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

    # ------------------------------------------------------------------
    # Super-admin plan, Phase 12
    # ------------------------------------------------------------------
    async def set_admin_status(self, *, email: str, is_admin: bool) -> User:
        user = await self.get_user_by_email(email=email)
        user.is_admin = is_admin
        await self.async_session.commit()
        await self.async_session.refresh(user)
        return user

    async def list_admins(self) -> list[User]:
        stmt = sqlalchemy.select(User).where(User.is_admin.is_(True))
        query = await self.async_session.execute(statement=stmt)
        return list(query.scalars().all())

    # ------------------------------------------------------------------
    # Bulk ATS staff role (recruiter / placement_staff) - see User.role and
    # api/dependencies/admin.py:get_current_bulk_ats_user. Mirrors
    # set_admin_status/list_admins above: an existing Samvaad admin grants
    # this directly (no auth-service bridge needed, unlike is_admin's
    # SamvaadAdminsTab path), gated by get_current_admin_user.
    # ------------------------------------------------------------------
    async def set_bulk_ats_role(self, *, email: str, role: str | None) -> User:
        user = await self.get_user_by_email(email=email)
        user.role = role
        await self.async_session.commit()
        await self.async_session.refresh(user)
        return user

    async def list_bulk_ats_staff(self) -> list[User]:
        stmt = sqlalchemy.select(User).where(User.role.in_(["recruiter", "placement_staff"]))
        query = await self.async_session.execute(statement=stmt)
        return list(query.scalars().all())

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
