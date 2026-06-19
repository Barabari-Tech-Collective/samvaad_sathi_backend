from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

# --- Static Metadata Structure ---
class TemplateCompactResponse(BaseModel):
    templateId: str
    name: str
    description: str
    previewImage: str
    tags: List[str]
    sections: List[str]

class TemplateDetailResponse(BaseModel):
    templateId: str
    name: str
    structure: Dict[str, List[str]]
    sampleData: Dict[str, Any]

# --- Structured Dynamic Data Models inside the Resume ---
class ResumeHeaderSchema(BaseModel):
    fullName: str = ""
    email: str = ""
    phone: str = ""
    linkedin: Optional[str] = ""
    github: Optional[str] = ""

class ResumeExperienceSchema(BaseModel):
    company: str = ""
    role: str = ""
    duration: str = ""
    highlights: List[str] = []

class ResumeProjectSchema(BaseModel):
    title: str = ""
    description: str = ""
    technologies: Optional[List[str]] = []
    duration: Optional[str] = ""
    bullets: Optional[List[str]] = []
    highlights: Optional[List[str]] = []
    githubUrl: Optional[str] = ""
    liveUrl: Optional[str] = ""

class ResumeEducationSchema(BaseModel):
    institution: str = ""
    degree: str = ""
    year: str = ""
    duration: Optional[str] = ""

class FullResumeContentSchema(BaseModel):
    header: ResumeHeaderSchema = Field(default_factory=ResumeHeaderSchema)
    summary: str = ""
    skills: List[str] = []
    experience: List[ResumeExperienceSchema] = []
    projects: List[ResumeProjectSchema] = []
    education: List[ResumeEducationSchema] = []

# --- Request/Response Payload Envelopes ---
class CreateResumeFromTemplateRequest(BaseModel):
    templateId: str = "default_ats_001"
    analysisId: Optional[str] = None

class UpdateResumeDataRequest(BaseModel):
    data: FullResumeContentSchema

class ResumeInstanceResponse(BaseModel):
    resumeId: int  
    userId: int    
    templateId: str
    data: FullResumeContentSchema
    status: str
    updatedAt: datetime

    class Config:
        from_attributes = True