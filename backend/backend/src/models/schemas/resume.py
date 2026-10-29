import typing

import pydantic

from src.models.schemas.base import BaseSchemaModel


class ValidatedResumeData(BaseSchemaModel):
    skills: list[str] = pydantic.Field(default_factory=list, description="Normalized, deduplicated skills for the user")
    years_experience: float | None = pydantic.Field(default=None, description="Validated total years of experience (0.0 - 50.0)")
    warnings: list[str] = pydantic.Field(default_factory=list, description="Validation warnings and fallback notes")


class EducationItem(BaseSchemaModel):
    degree: str | None = pydantic.Field(default=None)
    institution: str | None = pydantic.Field(default=None)
    start_date: str | None = pydantic.Field(default=None)
    end_date: str | None = pydantic.Field(default=None)


class ExperienceItem(BaseSchemaModel):
    company: str | None = pydantic.Field(default=None)
    role: str | None = pydantic.Field(default=None)
    start_date: str | None = pydantic.Field(default=None)
    end_date: str | None = pydantic.Field(default=None)
    responsibilities: list[str] | None = pydantic.Field(default=None)
    technologies: list[str] | None = pydantic.Field(default=None)


class ProjectItem(BaseSchemaModel):
    name: str | None = pydantic.Field(default=None)
    description: str | None = pydantic.Field(default=None)
    technologies: list[str] | None = pydantic.Field(default=None)
    link: str | None = pydantic.Field(default=None)


class ResumeDetails(BaseSchemaModel):
    full_name: str | None = pydantic.Field(default=None)
    email: str | None = pydantic.Field(default=None)
    phone: str | None = pydantic.Field(default=None)
    location: str | None = pydantic.Field(default=None)
    links: list[str] | None = pydantic.Field(default=None)
    summary: str | None = pydantic.Field(default=None)
    education: list[EducationItem] | None = pydantic.Field(default=None)
    experience: list[ExperienceItem] | None = pydantic.Field(default=None)
    projects: list[ProjectItem] | None = pydantic.Field(default=None)
    certifications: list[str] | None = pydantic.Field(default=None)
    languages: list[str] | None = pydantic.Field(default=None)
    job_titles: list[str] | None = pydantic.Field(default=None)
    companies: list[str] | None = pydantic.Field(default=None)


class ResumeStorageReceipt(BaseSchemaModel):
    file_sha256: str
    status: typing.Literal["registered", "unconfirmed"]
    resume_id: int | None = None
    delivery_status: typing.Literal["pending", "failed", "expired"] | None = None


class ResumeExtractionResponse(BaseSchemaModel):
    file_sha256: str | None = pydantic.Field(default=None, description="Exact uploaded bytes; use the account-scoped storage receipt endpoint to reconcile storage")
    filename: str = pydantic.Field(description="Original uploaded filename")
    content_type: str = pydantic.Field(description="MIME type of the uploaded file")
    size: int = pydantic.Field(description="File size in bytes (<= 5MB)")
    text_length: int = pydantic.Field(description="Length of the normalized extracted text")
    preview: str = pydantic.Field(description="First 300 characters of the normalized text")
    skills: list[str] = pydantic.Field(default_factory=list, description="Raw skills detected by LLM (pre-validation)")
    years_experience: float | None = pydantic.Field(default=None, description="Raw years experience detected by LLM (pre-validation)")
    llm_model: str | None = pydantic.Field(default=None, description="LLM model name used for extraction")
    llm_latency_ms: int | None = pydantic.Field(default=None, description="LLM call latency in milliseconds")
    llm_error: str | None = pydantic.Field(default=None, description="LLM error message if extraction failed")
    details: ResumeDetails | None = pydantic.Field(default=None, description="Extended structured resume details (contact, education, experience, projects, etc.)")
    validated: ValidatedResumeData = pydantic.Field(description="Validated and normalized extraction results")
    message: str = pydantic.Field(description="Human-friendly status message")
    saved: bool = pydantic.Field(description="Whether the user's profile was persisted with validated data")
    save_error: str | None = pydantic.Field(default=None, description="Error message when persistence fails")


class MyResumeResponse(BaseSchemaModel):
    user_id: int = pydantic.Field(description="User ID")
    email: pydantic.EmailStr = pydantic.Field(description="User email address")
    years_experience: float | None = pydantic.Field(default=None, description="Validated years of experience saved on user")
    skills: list[str] = pydantic.Field(default_factory=list, description="Normalized skills saved on user")
    has_resume_text: bool = pydantic.Field(description="Whether resume_text exists for the user")
    text_length: int = pydantic.Field(description="Length of saved resume_text if present")


class KnowledgeSet(BaseSchemaModel):
    items: list[str] = pydantic.Field(default_factory=list, description="Normalized skills derived for knowledge set")


class KnowledgeSetResponse(BaseSchemaModel):
    ok: bool = pydantic.Field(description="Indicates whether operation succeeded")
    cached: bool = pydantic.Field(description="Whether response is served from in-memory cache")
    knowledge_set: KnowledgeSet = pydantic.Field(description="Computed knowledge set")
    llm_model: str | None = pydantic.Field(default=None, description="LLM model used when applicable")
    llm_latency_ms: int | None = pydantic.Field(default=None, description="LLM latency in milliseconds")
    llm_error: str | None = pydantic.Field(default=None, description="LLM error message when fallback or cache was used")


