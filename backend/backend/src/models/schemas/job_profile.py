from pydantic import BaseModel
from typing import List, Optional
import datetime
import pydantic
from src.models.schemas.base import BaseSchemaModel

# --- feature/roles-page-api schemas ---
class JobProfileBase(BaseModel):
    title: str
    description: Optional[str] = None

class JobProfileCreateV2(JobProfileBase):
    pass

class JobProfileResponse(JobProfileBase):
    id: int
    created_at: datetime.datetime

    class Config:
        from_attributes = True

class JobProfileSummaryResponse(BaseModel):
    totalRoles: int
    pendingReview: int
    approved: int
    rejected: int

class JobProfileListResponse(BaseModel):
    items: List[JobProfileResponse]
    total: int

class JobProfileActivityResponse(BaseModel):
    id: int
    title: str
    action: str
    message: str
    createdAt: datetime.datetime

class JobProfileUploadResponse(BaseModel):
    success: bool
    originalFileName: str
    fileType: str
    fileSize: int

class JobProfileExtractSkillsRequest(BaseModel):
    jobDescription: str

class JobProfileExtractSkillsResponse(BaseModel):
    skills: List[str]

# --- upstream/master schemas ---
class JobProfileCreate(BaseSchemaModel):
    job_name: str = pydantic.Field(min_length=2, max_length=160)
    job_description: str = pydantic.Field(min_length=20)
    company_name: str | None = pydantic.Field(default=None, max_length=256)
    experience_level: str | None = pydantic.Field(default=None, max_length=64)
    skills: list[str] | None = None
    additional_context: str | None = None


class JobProfileOut(BaseSchemaModel):
    job_profile_id: int
    job_name: str
    job_description: str
    company_name: str | None = None
    experience_level: str | None = None
    skills: list[str] | None = None
    additional_context: str | None = None
    created_by: int | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class JobProfilesListResponse(BaseSchemaModel):
    items: list[JobProfileOut]


class JobProfileDeleteResponse(BaseSchemaModel):
    deleted: bool
    job_profile_id: int


# --- Generate Questions Schemas ---
class JobProfileQuestionLevelRequest(BaseModel):
    level: int
    count: int

class JobProfileGenerateQuestionsRequest(BaseModel):
    levels: List[JobProfileQuestionLevelRequest]

class JobProfileGeneratedQuestionItem(BaseModel):
    question_id: str
    question: str
    level: int
    difficulty: str
    type: str
    is_ai_generated: bool

class JobProfileGenerateQuestionsResponse(BaseModel):
    job_profile_id: str
    total_questions: int
    questions: List[JobProfileGeneratedQuestionItem]

