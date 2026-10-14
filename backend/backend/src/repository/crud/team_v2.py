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

        # 2. Check if user exists by email
        email_clean = member_data.email.strip().lower()
        stmt = select(User).where(User.email == email_clean)
        result = await self.async_session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            if not member_data.password:
                raise ValueError("Password is required when creating a new team member account.")
            hashed_pw = pwd_generator.generate_hashed_password(hash_salt="", new_password=member_data.password)
            user = User(
                email=email_clean,
                name=member_data.name.strip(),
                password_hash=hashed_pw, 
                is_onboarded=False,
                is_admin=False,
                is_super_admin=False,
            )
            self.async_session.add(user)
            await self.async_session.flush()
        # Security Note: If user already exists, NEVER overwrite their global password or email.

        # 3. Check existing membership (prevent duplicate rows / reactivate soft-deleted)
        mem_stmt = select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user.id)
        existing_member = (await self.async_session.execute(mem_stmt)).scalar_one_or_none()
        if existing_member:
            if existing_member.status == "active":
                raise EntityAlreadyExists("User is already an active member of this team.")
            # Reactivate soft-deleted member
            existing_member.status = "active"
            existing_member.removed_date = None
            existing_member.access_role = member_data.access_role
            existing_member.purposes = member_data.purposes
            existing_member.domain = member_data.domain
            existing_member.colleges = member_data.colleges
            new_member = existing_member
        else:
            new_member = TeamMember(
                team_id=team_id,
                user_id=user.id,
                access_role=member_data.access_role,
                purposes=member_data.purposes,
                domain=member_data.domain,
                colleges=member_data.colleges,
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

    async def update_team_member(self, team_id: int, member_id: int, member_data: TeamMemberUpdate) -> dict | None:
        stmt = select(TeamMember).where(TeamMember.id == member_id, TeamMember.team_id == team_id)
        result = await self.async_session.execute(stmt)
        member = result.scalar_one_or_none()
        if not member:
            return None
        
        if member_data.access_role is not None:
            member.access_role = member_data.access_role
        if member_data.purposes is not None:
            member.purposes = member_data.purposes
        if member_data.domain is not None:
            member.domain = member_data.domain
        if member_data.colleges is not None:
            member.colleges = member_data.colleges
            
        # Security Note: Never mutate user's email or password in team-scoped endpoint!
        stmt = select(User).where(User.id == member.user_id)
        result = await self.async_session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if user and member_data.name:
            user.name = member_data.name
            
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
