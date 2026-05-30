from pydantic import BaseModel
from typing import List, Optional
import datetime
import pydantic
from src.models.schemas.base import BaseSchemaModel

# --- feature/roles-page-api schemas ---
class JobProfileBase(BaseSchemaModel):
    job_name: str = pydantic.Field(
        validation_alias=pydantic.AliasChoices("job_name", "jobName", "title"),
        serialization_alias="jobName"
    )
    job_description: Optional[str] = pydantic.Field(
        default=None,
        validation_alias=pydantic.AliasChoices("job_description", "jobDescription", "description"),
        serialization_alias="jobDescription"
    )
    company_name: Optional[str] = None
    experience_level: Optional[str] = None
    skills: Optional[List[str]] = None
    additional_context: Optional[str] = None

class JobProfileCreateV2(JobProfileBase):
    pass

class JobProfileResponse(JobProfileBase):
    id: int
    created_at: datetime.datetime

    @pydantic.computed_field
    @property
    def title(self) -> str:
        return self.job_name

    @pydantic.computed_field
    @property
    def description(self) -> Optional[str]:
        return self.job_description

class JobProfileSummaryResponse(BaseSchemaModel):
    total_roles: int
    pending_review: int
    approved: int
    rejected: int

class JobProfileListResponse(BaseSchemaModel):
    items: List[JobProfileResponse]
    total: int

class JobProfileActivityResponse(BaseSchemaModel):
    id: int
    title: str
    action: str
    message: str
    created_at: datetime.datetime

class JobProfileUploadResponse(BaseSchemaModel):
    success: bool
    original_file_name: str
    file_type: str
    file_size: int

class JobProfileExtractSkillsRequest(BaseSchemaModel):
    job_description: str

class JobProfileExtractSkillsResponse(BaseSchemaModel):
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


# --- Get Questions Schemas ---
class JobProfileQuestionLevelCounts(BaseModel):
    level_1: int = 0
    level_2: int = 0
    level_3: int = 0
    level_4: int = 0

class JobProfileQuestionItem(BaseModel):
    question_id: str
    question: str
    level: int
    difficulty: str
    type: str
    is_ai_generated: bool
    created_at: datetime.datetime

class JobProfileQuestionsListResponse(BaseModel):
    job_profile_id: str
    total_questions: int
    level_counts: JobProfileQuestionLevelCounts
    questions: List[JobProfileQuestionItem]


# --- Add Question Schemas ---
class JobProfileAddQuestionRequest(BaseModel):
    question: str
    level: int
    difficulty: str
    type: str = "theoretical"
    is_ai_generated: bool = False

class JobProfileAddQuestionResponse(BaseModel):
    question_id: str
    job_profile_id: str
    question: str
    level: int
    difficulty: str
    type: str
    is_ai_generated: bool
    message: str


# --- Update Question Schemas ---
class JobProfileUpdateQuestionRequest(BaseModel):
    question: Optional[str] = None
    level: Optional[int] = None
    difficulty: Optional[str] = None
    type: Optional[str] = None

class JobProfileUpdateQuestionResponse(BaseModel):
    question_id: str
    question: str
    level: int
    difficulty: str
    type: str
    is_ai_generated: bool
    message: str


# --- Regenerate Question Schemas ---
class JobProfileRegenerateQuestionResponse(BaseModel):
    question_id: str
    question: str
    level: int
    difficulty: str
    type: str
    is_ai_generated: bool
    message: str


# --- Delete Question Schemas ---
class JobProfileDeleteQuestionResponse(BaseModel):
    message: str
    question_id: str






