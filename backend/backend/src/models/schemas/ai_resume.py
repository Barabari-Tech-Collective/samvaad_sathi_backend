from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class ScoreBreakdown(BaseModel):
    skillsMatch: int
    experienceMatch: int  
    formattingScore: int  
    keywordDensity: int

class SkillsAnalysis(BaseModel):
    strongSkills: List[str]
    missingSkills: List[str]
    deprioritizedSkills: List[str]

class ExperienceEvaluationSchema(BaseModel):
    rating: str  # Excellent | Good | Average | Bad
    feedback: str

# Added schema to back education tracking requirements 
class EducationEvaluationSchema(BaseModel):
    hasInstitution: bool
    hasDuration: bool
    hasScore: bool
    rating: str          # Excellent | Good | Needs_Improvement
    feedback: str

class ProjectEvaluationItem(BaseModel):
    projectName: str
    rating: str
    feedback: str
    projectUrl: Optional[str] = None 

class SuggestedProjectSchema(BaseModel):
    title: str
    description: str
    difficulty: str
    tags: List[str]

class HygieneCheck(BaseModel):
    grammarIssues: List[str]
    hasLinkedIn: bool
    hasGithub: bool
    hasPortfolio: bool
    hasPhone: bool
    hasEmail: bool

class ResumeAnalysisResponse(BaseModel):
    analysisId: str
    atsScore: int
    summary: str
    scoreBreakdown: ScoreBreakdown
    skillsAnalysis: SkillsAnalysis
    experienceEvaluation: ExperienceEvaluationSchema
    educationEvaluation: EducationEvaluationSchema
    projectEvaluation: List[ProjectEvaluationItem]
    suggestedProject: Optional[SuggestedProjectSchema] = None
    finalRecommendations: List[str]
    hygieneCheck: HygieneCheck
    verifiedLinksMetadata: Optional[Dict[str, Any]] = None