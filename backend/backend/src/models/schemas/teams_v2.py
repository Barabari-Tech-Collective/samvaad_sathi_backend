import datetime
import enum
from typing import List, Optional
from pydantic import Field, field_validator

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
    password: Optional[str] = Field(
        None,
        min_length=8,
        max_length=128,
        description="Password for user credentials (minimum 8 characters)",
    )

    @field_validator("password", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        if isinstance(v, str) and not v.strip():
            return None
        return v


class TeamMemberUpdate(BaseSchemaModel):
    name: Optional[str] = Field(None, description="Name of the user")
    access_role: Optional[str] = Field(None, description="Access role for the user")
    purposes: Optional[List[str]] = Field(None, description="Granular access purposes")
    domain: Optional[str] = Field(None, description="Assigned domain")
    colleges: Optional[List[str]] = Field(None, description="Assigned colleges")
    password: Optional[str] = Field(
        None,
        min_length=8,
        max_length=128,
        description="Password to update user credentials (minimum 8 characters)",
    )

    @field_validator("password", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        if isinstance(v, str) and not v.strip():
            return None
        return v




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
