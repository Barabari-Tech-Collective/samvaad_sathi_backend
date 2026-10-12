import datetime
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload

from src.repository.crud.base import BaseCRUDRepository
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.models.schemas.teams_v2 import TeamCreate, TeamMemberCreate, TeamMemberUpdate
from src.securities.hashing.password import pwd_generator

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

    async def delete_team(self, team_id: int) -> str:
        # Check if the team has any active members
        stmt = select(func.count(TeamMember.id)).where(TeamMember.team_id == team_id, TeamMember.status == 'active')
        result = await self.async_session.execute(stmt)
        active_member_count = result.scalar() or 0
        
        if active_member_count > 0:
            return "has_members"
            
        stmt_team = select(Team).where(Team.id == team_id)
        result_team = await self.async_session.execute(stmt_team)
        team = result_team.scalar_one_or_none()
        
        if not team:
            return "not_found"
            
        # Soft-delete the team by updating status to 'deleted'
        team.status = "deleted"
        await self.async_session.commit()
        return "success"

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
                "avatar": None,  # Assuming avatar logic is separate or not in user model directly
                "access_role": member.access_role,
                "purposes": member.purposes,
                "domain": member.domain,
                "colleges": member.colleges,
                "status": member.status,
                "removed_date": member.removed_date
            })
        return members

    async def add_team_member(self, team_id: int, member_data: TeamMemberCreate) -> dict:
        # First, find or create user based on email (simplified for this repo logic)
        # Ideally user creation is handled by another service if they don't exist
        stmt = select(User).where(User.email == member_data.email)
        result = await self.async_session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            raw_password = member_data.password or "Welcome@123"
            hashed_pw = pwd_generator.generate_hashed_password(hash_salt="", new_password=raw_password)
            user = User(
                email=member_data.email,
                name=member_data.name,
                password_hash=hashed_pw, 
                is_onboarded=False,
                is_admin=False  # Regular team members should NOT be global admins
            )
            self.async_session.add(user)
            await self.async_session.flush()
        else:
            # Update name and password if user already exists
            if member_data.name and user.name != member_data.name:
                user.name = member_data.name
            if member_data.password:
                user.password_hash = pwd_generator.generate_hashed_password(hash_salt="", new_password=member_data.password)
            await self.async_session.flush()

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
        member.removed_date = datetime.datetime.utcnow()
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
            
        stmt = select(User).where(User.id == member.user_id)
        result = await self.async_session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if user:
            if member_data.name:
                user.name = member_data.name
            if member_data.email:
                user.email = member_data.email
            if member_data.password:
                user.password_hash = pwd_generator.generate_hashed_password(hash_salt="", new_password=member_data.password)
            
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
