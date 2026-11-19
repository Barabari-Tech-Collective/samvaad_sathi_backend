from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class ScoreBreakdown(BaseModel):
    skillsMatch: int
    experienceMatch: int  
    projectMatch: Optional[int] = 0 
    linkIntegrity: int
    educationScore: int
    totalMonths: Optional[int] = 0     
    isFresher: Optional[bool] = False  

class SkillsAnalysis(BaseModel):
    strongSkills: List[str]
    missingSkills: List[str]
    additionalSkills: List[str]

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
    grammarIssues: List[str] = []
    hasLinkedIn: bool = False
    hasGithub: bool = False
    hasPortfolio: bool = False
    hasPhone: bool = False
    hasEmail: bool = False
    hasInstitution: Optional[bool] = False
    hasDuration: Optional[bool] = False
    hasScore: Optional[bool] = False
    hasCgpaOrPercentage: Optional[bool] = False
    detectedScore: Optional[str] = None
    detectedCgpaOrPercentage: Optional[str] = None
    scoreMessage: Optional[str] = None

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