import datetime

import pydantic

from src.models.schemas.base import BaseSchemaModel


class UserCreate(BaseSchemaModel):
    email: pydantic.EmailStr
    password: str
    name: str


class UserLogin(BaseSchemaModel):
    email: pydantic.EmailStr
    password: str


# ------------------------------
# Profile update schemas
# ------------------------------

from src.models.db.user import TargetPositionEnum


class UserProfileUpdate(BaseSchemaModel):
    degree: str | None = None
    university: str | None = None
    target_position: str | None = None
    years_experience: float | None = None

    # profile_picture is not included here because it is uploaded as multipart file.


class UserProfileOut(BaseSchemaModel):
    user_id: int = pydantic.Field(description="Unique identifier for the user")
    email: pydantic.EmailStr
    name: str
    degree: str | None
    university: str | None
    target_position: str | None
    years_experience: float | None
    has_resume_text: bool = False
    skills: list[str] | None = None
    # company removed from response


class UserWithToken(BaseSchemaModel):
    token: str
    refresh_token: str | None = None
    email: pydantic.EmailStr
    name: str
    created_at: datetime.datetime
    is_onboarded: bool
    degree: str | None
    university: str | None
    target_position: str | None
    years_experience: float | None
    has_resume: bool = False
    total_attempts: int = pydantic.Field(default=0, ge=0, description="Total number of summary reports (attempts)")
    has_resume_text: bool = False
    skills: list[str] | None = None
    # company removed from response


class UserInResponse(BaseSchemaModel):
    user_id: int = pydantic.Field(description="Unique identifier for the user")
    authorized_user: UserWithToken


