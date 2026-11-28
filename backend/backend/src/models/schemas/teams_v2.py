import datetime
import enum
from typing import List, Optional
import pydantic
from pydantic import Field, field_validator

from src.models.schemas.base import BaseSchemaModel
from src.services.staff_roles import canonical_role


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
    purposes: Optional[List[str]] = Field(default_factory=list, description="Selected purposes for the team")


class TeamCreate(TeamBase):
    pass


class TeamResponse(TeamBase):
    id: int
    status: str
    member_count: int = Field(0, alias="memberCount", description="Total active members in the team")
    created_at: datetime.datetime = Field(alias="createdAt")
    purposes: List[str] = Field(default_factory=list, description="Array of purposes")
    created_by_name: str = Field("Super Admin", alias="createdByName", description="Full name of the user who created the team")

    @pydantic.computed_field(alias="member_count")
    @property
    def member_count_snake(self) -> int:
        return self.member_count

    @pydantic.computed_field(alias="created_by_name")
    @property
    def created_by_name_snake(self) -> str:
        return self.created_by_name

    @pydantic.computed_field(alias="created_at")
    @property
    def created_at_snake(self) -> datetime.datetime:
        return self.created_at


class TeamMemberBase(BaseSchemaModel):
    access_role: str = Field(..., description="Access role for the user (e.g., Creator, Facilitator)")
    purposes: Optional[List[str]] = Field(None, description="Granular access purposes")
    domain: Optional[str] = Field(None, description="Assigned domain")
    colleges: Optional[List[str]] = Field(None, description="Assigned colleges")


class TeamMemberCreate(TeamMemberBase):
    @field_validator("access_role")
    @classmethod
    def supported_role(cls, value: str) -> str:
        return canonical_role(value)

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

    @field_validator("access_role")
    @classmethod
    def supported_role(cls, value: str | None) -> str | None:
        return canonical_role(value) if value is not None else None

    @field_validator("password", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        if isinstance(v, str) and not v.strip():
            return None
        return v


class TeamMemberResponse(TeamMemberBase):
    id: int
    user_id: int = Field(alias="userId")
    team_id: int = Field(alias="teamId")
    name: str
    email: str
    avatar: Optional[str] = Field(None, description="URL or placeholder for avatar")
    status: str
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc),
        alias="createdAt",
    )
    created_by_name: str = Field("Super Admin", alias="createdByName")
    purposes: List[str] = Field(default_factory=list)
    colleges: List[str] = Field(default_factory=list)
    domain: Optional[str] = None

    @pydantic.computed_field(alias="user_id")
    @property
    def user_id_snake(self) -> int:
        return self.user_id

    @pydantic.computed_field(alias="team_id")
    @property
    def team_id_snake(self) -> int:
        return self.team_id

    @pydantic.computed_field(alias="access_role")
    @property
    def access_role_snake(self) -> str:
        return self.access_role

    @pydantic.computed_field(alias="created_at")
    @property
    def created_at_snake(self) -> datetime.datetime:
        return self.created_at

    @pydantic.computed_field(alias="created_by_name")
    @property
    def created_by_name_snake(self) -> str:
        return self.created_by_name


class TeamMemberRecycleBinResponse(TeamMemberResponse):
    removed_date: Optional[datetime.datetime] = Field(None, alias="removedDate")

    @pydantic.computed_field(alias="removed_date")
    @property
    def removed_date_snake(self) -> Optional[datetime.datetime]:
        return self.removed_date


class RoleDistribution(BaseSchemaModel):
    creator: int = 0
    sr_facilitator: int = 0
    jr_facilitator: int = 0
    team_member: int = 0

    @pydantic.computed_field(alias="sr_facilitator")
    @property
    def sr_facilitator_snake(self) -> int:
        return self.sr_facilitator

    @pydantic.computed_field(alias="jr_facilitator")
    @property
    def jr_facilitator_snake(self) -> int:
        return self.jr_facilitator

    @pydantic.computed_field(alias="team_member")
    @property
    def team_member_snake(self) -> int:
        return self.team_member


class TeamSummaryItem(BaseSchemaModel):
    id: int
    name: str
    lead_name: Optional[str] = Field(None, alias="leadName")
    lead_email: Optional[str] = Field(None, alias="leadEmail")
    purposes: List[str] = Field(default_factory=list)
    member_count: int = Field(0, alias="memberCount", description="Count of active members")
    created_by_name: str = Field("Super Admin", alias="createdByName")
    created_at: datetime.datetime = Field(alias="createdAt")

    @pydantic.computed_field(alias="member_count")
    @property
    def member_count_snake(self) -> int:
        return self.member_count

    @pydantic.computed_field(alias="created_by_name")
    @property
    def created_by_name_snake(self) -> str:
        return self.created_by_name

    @pydantic.computed_field(alias="created_at")
    @property
    def created_at_snake(self) -> datetime.datetime:
        return self.created_at

    @pydantic.computed_field(alias="lead_name")
    @property
    def lead_name_snake(self) -> Optional[str]:
        return self.lead_name

    @pydantic.computed_field(alias="lead_email")
    @property
    def lead_email_snake(self) -> Optional[str]:
        return self.lead_email


class TeamsSummaryResponse(BaseSchemaModel):
    total_teams: int = Field(0, alias="totalTeams")
    total_active_members: int = Field(0, alias="totalActiveMembers")
    role_distribution: RoleDistribution = Field(alias="roleDistribution")
    teams: List[TeamSummaryItem] = Field(default_factory=list)

    @pydantic.computed_field(alias="total_teams")
    @property
    def total_teams_snake(self) -> int:
        return self.total_teams

    @pydantic.computed_field(alias="total_active_members")
    @property
    def total_active_members_snake(self) -> int:
        return self.total_active_members

    @pydantic.computed_field(alias="role_distribution")
    @property
    def role_distribution_snake(self) -> RoleDistribution:
        return self.role_distribution
