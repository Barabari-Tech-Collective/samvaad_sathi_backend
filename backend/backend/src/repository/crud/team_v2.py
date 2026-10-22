import datetime
import enum
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload

from src.repository.crud.base import BaseCRUDRepository
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.models.schemas.teams_v2 import TeamCreate, TeamMemberCreate, TeamMemberUpdate
from src.securities.hashing.password import pwd_generator
from src.utilities.exceptions.database import EntityDoesNotExist, EntityAlreadyExists
from src.api.dependencies.admin import is_admin_user, is_super_admin_user
from src.services.central_auth_service import (
    provision_or_invite_staff,
    CentralAuthProvisioningError,
    CentralAuthReconciliationError,
)


class TeamDeleteStatus(str, enum.Enum):
    SUCCESS = "success"
    HAS_MEMBERS = "has_members"
    NOT_FOUND = "not_found"


class TeamCRUDRepository(BaseCRUDRepository):
    
    async def get_all_teams(self, status: str = "active") -> list[dict]:
        """Fetch all teams with their active member counts, filtered by status."""
        stmt = (
            select(
                Team,
                func.count(TeamMember.id).filter(TeamMember.status == 'active').label("memberCount")
            )
            .outerjoin(TeamMember, Team.id == TeamMember.team_id)
        )
        if status:
            stmt = stmt.where(Team.status == status)

        stmt = (
            stmt
            .group_by(Team.id)
            .order_by(Team.created_at.desc())
        )
        result = await self.async_session.execute(stmt)
        rows = result.all()
        
        teams = []
        for team, member_count in rows:
            teams.append({
                "id": team.id,
                "name": team.name,
                "description": team.description,
                "status": team.status,
                "created_at": team.created_at,
                "memberCount": member_count
            })
        return teams

    async def create_team(self, team_data: TeamCreate) -> dict:
        new_team = Team(
            name=team_data.name,
            description=team_data.description,
            status="active"
        )
        self.async_session.add(new_team)
        await self.async_session.commit()
        await self.async_session.refresh(new_team)
        
        return {
            "id": new_team.id,
            "name": new_team.name,
            "description": new_team.description,
            "status": new_team.status,
            "created_at": new_team.created_at,
            "memberCount": 0
        }

    async def delete_team(self, team_id: int) -> TeamDeleteStatus:
        # Check if the team has any active members
        stmt = select(func.count(TeamMember.id)).where(TeamMember.team_id == team_id, TeamMember.status == 'active')
        result = await self.async_session.execute(stmt)
        active_member_count = result.scalar() or 0
        
        if active_member_count > 0:
            return TeamDeleteStatus.HAS_MEMBERS
            
        stmt_team = select(Team).where(Team.id == team_id)
        result_team = await self.async_session.execute(stmt_team)
        team = result_team.scalar_one_or_none()
        
        if not team:
            return TeamDeleteStatus.NOT_FOUND
            
        # Soft-delete the team by updating status to 'deleted'
        team.status = "deleted"
        await self.async_session.commit()
        return TeamDeleteStatus.SUCCESS

    async def restore_team(self, team_id: int) -> bool:
        stmt_team = select(Team).where(Team.id == team_id)
        result_team = await self.async_session.execute(stmt_team)
        team = result_team.scalar_one_or_none()
        
        if not team:
            return False
            
        team.status = "active"
        await self.async_session.commit()
        return True

    async def permanent_delete_team(self, team_id: int) -> bool:
        stmt_team = select(Team).where(Team.id == team_id)
        result_team = await self.async_session.execute(stmt_team)
        team = result_team.scalar_one_or_none()
        
        if not team:
            return False
            
        await self.async_session.delete(team)
        await self.async_session.commit()
        return True

    async def get_team_members(self, team_id: int, status: str = 'active') -> list[dict]:
        stmt = (
            select(TeamMember, User)
            .join(User, TeamMember.user_id == User.id)
            .where(TeamMember.team_id == team_id)
            .where(TeamMember.status == status)
            .order_by(TeamMember.created_at.desc())
        )
        result = await self.async_session.execute(stmt)
        rows = result.all()
        
        members = []
        for member, user in rows:
            members.append({
                "id": member.id,
                "user_id": user.id,
                "team_id": member.team_id,
                "name": user.name,
                "email": user.email,
                "avatar": None,
                "access_role": member.access_role,
                "purposes": member.purposes,
                "domain": member.domain,
                "colleges": member.colleges,
                "status": member.status,
                "removed_date": member.removed_date
            })
        return members

    async def add_team_member(self, team_id: int, member_data: TeamMemberCreate) -> dict:
        # 1. Verify team exists and is active
        team_stmt = select(Team).where(Team.id == team_id)
        team = (await self.async_session.execute(team_stmt)).scalar_one_or_none()
        if not team or team.status != "active":
            raise EntityDoesNotExist("Team not found or is inactive")

        clean_role = (member_data.access_role or "").strip().replace("_", " ").lower()
        if clean_role in {"jr facilitator", "junior facilitator"}:
            sanitized_colleges = (
                [c.strip() for c in member_data.colleges if isinstance(c, str) and c.strip()]
                if member_data.colleges is not None
                else []
            )
            if not sanitized_colleges:
                raise ValueError("Assigned colleges are required when adding a Junior Facilitator.")
            assigned_colleges = sanitized_colleges
        else:
            # Team Members and Creators are not assigned specific colleges (they view all colleges)
            assigned_colleges = None

        # 2. Check if user exists by email
        email_clean = member_data.email.strip().lower()
        stmt = select(User).where(func.lower(User.email) == email_clean)
        result = await self.async_session.execute(stmt)
        user = result.scalar_one_or_none()

        # 3. Check existing membership BEFORE any mutations (prevent duplicate rows / session pollution)
        existing_member = None
        if user:
            mem_stmt = select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user.id)
            existing_member = (await self.async_session.execute(mem_stmt)).scalar_one_or_none()
            if existing_member and existing_member.status == "active":
                raise EntityAlreadyExists("User is already an active member of this team.")

        # 4. Handle Central Auth provisioning (Central Auth is source of truth for credentials)
        # Any failure in Central Auth provisioning raises CentralAuthProvisioningError and aborts
        # before any local user or team membership mutations occur.
        central_role = (
            "FACILITATOR" if member_data.access_role and "facilitator" in member_data.access_role.lower()
            else "STAFF"
        )
        central_user_id = await provision_or_invite_staff(
            email=email_clean,
            name=member_data.name.strip(),
            role=central_role,
            send_invite_email=True,
            password=member_data.password.strip() if (member_data.password and member_data.password.strip()) else None,
        )

        # 5. Reconcile existing local user binding with central_user_id BEFORE mutating membership.
        # If the local user already has a student_id that conflicts with central_user_id, abort immediately.
        if user:
            if user.student_id and user.student_id != central_user_id:
                raise CentralAuthReconciliationError(
                    f"User with email '{email_clean}' is already linked to Central Auth ID '{user.student_id}', "
                    f"which does not match provisioned ID '{central_user_id}'. Manual reconciliation required."
                )
            if not user.student_id:
                user.student_id = central_user_id

            # If user has no password yet and an initial password was provided, initialize it
            if member_data.password and member_data.password.strip():
                is_uninitialized = not user.password_hash or user.password_hash.strip() == ""
                if is_uninitialized and not (is_admin_user(user) or is_super_admin_user(user)):
                    user.password_hash = pwd_generator.generate_hashed_password(
                        hash_salt="", new_password=member_data.password.strip()
                    )
        else:
            hashed_pw = (
                pwd_generator.generate_hashed_password(hash_salt="", new_password=member_data.password.strip())
                if (member_data.password and member_data.password.strip())
                else ""
            )
            user = User(
                email=email_clean,
                name=member_data.name.strip(),
                student_id=central_user_id,
                password_hash=hashed_pw,
                role=None,
                is_onboarded=False,
                is_admin=False,
                is_super_admin=False,
            )
            self.async_session.add(user)
            await self.async_session.flush()

        # 5. Create or reactivate TeamMember record
        if existing_member:
            # Reactivate soft-deleted member
            existing_member.status = "active"
            existing_member.removed_date = None
            existing_member.access_role = member_data.access_role
            existing_member.purposes = member_data.purposes
            existing_member.domain = member_data.domain
            existing_member.colleges = assigned_colleges
            new_member = existing_member
        else:
            new_member = TeamMember(
                team_id=team_id,
                user_id=user.id,
                access_role=member_data.access_role,
                purposes=member_data.purposes,
                domain=member_data.domain,
                colleges=assigned_colleges,
                status='active'
            )
            self.async_session.add(new_member)

        await self.async_session.commit()
        await self.async_session.refresh(new_member)
        
        return {
            "id": new_member.id,
            "user_id": user.id,
            "team_id": new_member.team_id,
            "name": user.name,
            "email": user.email,
            "avatar": None,
            "access_role": new_member.access_role,
            "purposes": new_member.purposes,
            "domain": new_member.domain,
            "colleges": new_member.colleges,
            "status": new_member.status,
            "removed_date": new_member.removed_date
        }

    async def soft_delete_member(self, team_id: int, member_id: int) -> bool:
        stmt = select(TeamMember).where(TeamMember.id == member_id, TeamMember.team_id == team_id)
        result = await self.async_session.execute(stmt)
        member = result.scalar_one_or_none()
        if not member:
            return False
            
        member.status = 'deleted'
        member.removed_date = datetime.datetime.now(datetime.timezone.utc)
        await self.async_session.commit()
        return True

    async def restore_member(self, team_id: int, member_id: int) -> bool:
        stmt = select(TeamMember).where(TeamMember.id == member_id, TeamMember.team_id == team_id)
        result = await self.async_session.execute(stmt)
        member = result.scalar_one_or_none()
        if not member:
            return False
            
        member.status = 'active'
        member.removed_date = None
        await self.async_session.commit()
        return True

    async def permanent_delete_member(self, team_id: int, member_id: int) -> bool:
        stmt = select(TeamMember).where(TeamMember.id == member_id, TeamMember.team_id == team_id)
        result = await self.async_session.execute(stmt)
        member = result.scalar_one_or_none()
        if not member:
            return False
            
        await self.async_session.delete(member)
        await self.async_session.commit()
        return True

    async def update_team_member(
        self,
        team_id: int,
        member_id: int,
        member_data: TeamMemberUpdate,
        caller_user: User | None = None,
    ) -> dict | None:
        stmt = select(TeamMember).where(TeamMember.id == member_id, TeamMember.team_id == team_id)
        result = await self.async_session.execute(stmt)
        member = result.scalar_one_or_none()
        if not member:
            return None
        
        raw_role = member_data.access_role if member_data.access_role is not None else member.access_role
        effective_role = (raw_role or "").strip().replace("_", " ").lower()
        is_updating_to_jf = (
            member_data.access_role is not None
            and effective_role in {"jr facilitator", "junior facilitator"}
        )

        if effective_role in {"team member", "member", "staff"}:
            member.colleges = None
        elif effective_role in {"jr facilitator", "junior facilitator"}:
            if member_data.colleges is not None:
                clean_colleges = [c.strip() for c in member_data.colleges if isinstance(c, str) and c.strip()]
                if not clean_colleges:
                    if is_updating_to_jf:
                        raise ValueError("Assigned colleges are required when changing role to Junior Facilitator.")
                    else:
                        raise ValueError("Assigned colleges are required for Junior Facilitators.")
                member.colleges = clean_colleges
            elif is_updating_to_jf and (
                not member.colleges
                or not isinstance(member.colleges, list)
                or not any(isinstance(c, str) and c.strip() for c in member.colleges)
            ):
                raise ValueError("Assigned colleges are required when changing role to Junior Facilitator.")
        elif member_data.colleges is not None:
            member.colleges = [c.strip() for c in member_data.colleges if isinstance(c, str) and c.strip()]

        if member_data.access_role is not None:
            member.access_role = member_data.access_role
        if member_data.purposes is not None:
            member.purposes = member_data.purposes
        if member_data.domain is not None:
            member.domain = member_data.domain
            
        stmt = select(User).where(User.id == member.user_id)
        result = await self.async_session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if user:
            if member_data.name:
                user.name = member_data.name
            if member_data.password and member_data.password.strip():
                # Permission check: resetting credentials via team endpoints requires administrator privileges (fail-closed)
                if caller_user is None or not is_admin_user(caller_user):
                    raise ValueError("Administrator access required to reset member credentials.")

                # Account protection: administrative accounts can never be reset via team member settings
                if is_admin_user(user) or is_super_admin_user(user):
                    raise ValueError("Cannot reset credentials for administrative accounts via team member settings.")

                user.password_hash = pwd_generator.generate_hashed_password(
                    hash_salt="", new_password=member_data.password.strip()
                )
                try:
                    await provision_or_invite_staff(
                        email=user.email,
                        name=user.name or "",
                        role="STAFF",
                        send_invite_email=False,
                        password=member_data.password.strip(),
                    )
                except Exception as exc:
                    logger.warning(f"[central-auth] Could not sync password reset to Central Auth for {user.email}: {exc}")
            
        await self.async_session.commit()
        await self.async_session.refresh(member)
        if user:
            await self.async_session.refresh(user)
            
        return {
            "id": member.id,
            "user_id": member.user_id,
            "team_id": member.team_id,
            "name": user.name if user else "",
            "email": user.email if user else "",
            "avatar": None,
            "access_role": member.access_role,
            "purposes": member.purposes,
            "domain": member.domain,
            "colleges": member.colleges,
            "status": member.status,
            "removed_date": member.removed_date
        }
