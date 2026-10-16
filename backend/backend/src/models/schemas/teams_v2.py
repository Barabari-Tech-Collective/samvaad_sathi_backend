import datetime
import enum
from typing import List, Optional
from pydantic import Field

from src.models.schemas.base import BaseSchemaModel


class TeamRoleEnum(str, enum.Enum):
    LEAD = "lead"
    MEMBER = "member"
    CREATOR = "creator"
    FACILITATOR = "facilitator"
    ADMIN = "admin"


class TeamStatusEnum(str, enum.Enum):
    ACTIVE = "active"
    DELETED = "deleted"


class TeamBase(BaseSchemaModel):
    name: str = Field(..., description="Name of the team")
    description: Optional[str] = Field(None, description="Description of the team")


class TeamCreate(TeamBase):
    pass


class TeamResponse(TeamBase):
    id: int
    status: str
    member_count: int = Field(0, alias="memberCount", description="Total active members in the team")
    created_at: datetime.datetime


class TeamMemberBase(BaseSchemaModel):
    access_role: str = Field(..., description="Access role for the user (e.g., Creator, Facilitator)")
    purposes: Optional[List[str]] = Field(None, description="Granular access purposes")
    domain: Optional[str] = Field(None, description="Assigned domain")
    colleges: Optional[List[str]] = Field(None, description="Assigned colleges")


class TeamMemberCreate(TeamMemberBase):
    name: str = Field(..., description="Name of the user to be added")
    email: str = Field(..., description="Email of the user to be added")
    password: Optional[str] = Field(None, description="Password if creating a new user")


class TeamMemberUpdate(BaseSchemaModel):
    name: Optional[str] = Field(None, description="Name of the user")
    access_role: Optional[str] = Field(None, description="Access role for the user")
    purposes: Optional[List[str]] = Field(None, description="Granular access purposes")
    domain: Optional[str] = Field(None, description="Assigned domain")
    colleges: Optional[List[str]] = Field(None, description="Assigned colleges")




class TeamMemberResponse(TeamMemberBase):
    id: int
    user_id: int
    team_id: int
    name: str
    email: str
    avatar: Optional[str] = Field(None, description="URL or placeholder for avatar")
    status: str


class TeamMemberRecycleBinResponse(TeamMemberResponse):
    removed_date: Optional[datetime.datetime] = None
