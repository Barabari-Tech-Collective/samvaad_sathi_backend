import json
import uuid

from fastapi import HTTPException
from openai import AsyncOpenAI

from src.config.manager import settings
from src.utilities.link_validator import SmartLinkValidator
from src.services.ai_resume.scoring.ats_engine import ATSEngine
from src.services.ai_resume.prompt_builder import (
    build_ats_analysis_prompt,
)

# Initialize OpenAI client
client = AsyncOpenAI(
    api_key=settings.OPENAI_API_KEY,
)

ats_engine = ATSEngine()
async def generate_ats_analysis(
    resume_text: str,
    target_role: str,
    experience_level: str,
    job_description: str,
    parsed_resume_json: dict,
):
    """
    Generates ATS analysis using OpenAI.
    Executes the split-pipeline production ATS Assessment.
    Pipeline 1 (Python): 100% Deterministic evaluation matrices and link scoring logic.
    Pipeline 2 (OpenAI): Generates natural language insights, recommendations, and project paths.

    """

    try:
        # 1. Run our real-time network Link Validator Pipeline first (Async/Non-blocking)
        link_validator = SmartLinkValidator()
        verified_links_context = await link_validator.validate_all_links_async(resume_text)
        # 2. Safely extract tracking structures from your existing parsed resume block
        parsed_skills = parsed_resume_json.get("skills", [])
        parsed_education = parsed_resume_json.get("education", [])
        parsed_projects = parsed_resume_json.get("projects", [])
        parsed_experience = parsed_resume_json.get("experience", [])
        
        # 3. Execute the Deterministic Scoring Pipeline
        deterministic_report = ats_engine.run_assessment(
            verified_links_raw=verified_links_context,
            parsed_skills=parsed_skills,
            jd_skills=[],
            parsed_education=parsed_education,
            parsed_projects=parsed_projects,
            parsed_experience=parsed_experience,
            experience_level=experience_level,
            target_role=target_role,
            raw_resume_text=resume_text,
            raw_jd_text=job_description
        )
    


        # 3. Build the comprehensive structured prompt using our dynamic matrices
        # Build prompt
        prompt = build_ats_analysis_prompt(
            resume_text=resume_text,
            deterministic_report=deterministic_report,
            target_role=target_role,
            experience_level=experience_level,
            job_description=job_description,
        )
        # 4. Fire the complete execution context request to OpenAI
        # Call OpenAI
        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional ATS resume evaluator. Use your natural language capabilities exclusively to explain provided scores. Output only raw JSON formats."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )

        # Extract AI content
        ai_response = response.choices[0].message.content

        if not ai_response:
            raise HTTPException(
                status_code=500,
                detail="Empty AI response received",
            )
        # Convert JSON string to Python dict
        # Convert JSON string to Python dict
        parsed_ai_feedback = json.loads(ai_response)
        
        python_projects = deterministic_report.get("deterministicMetrics", {}).get("projectModule", {}).get("projectEvaluation", [])
        ai_project_feedback = parsed_ai_feedback.get("projectEvaluation", [])

        # 5. Composite Fusion: Merge Python scores and case-isolated AI project narratives safely
        final_response = {
            "analysisId": str(uuid.uuid4()),
            "atsScore": deterministic_report["atsScore"],
            "overallRating": deterministic_report["overallRating"],
            "uiThemeColor": deterministic_report["uiThemeColor"],
            "summary": parsed_ai_feedback.get("summary", "Analysis processing complete."),
            "scoreBreakdown": deterministic_report["scoreBreakdown"],
            
            "skillsAnalysis": deterministic_report["deterministicMetrics"]["skillsModule"]["skillsAnalysis"],
            
            "experienceEvaluation": {
                "rating": deterministic_report["deterministicMetrics"]["experienceModule"]["experienceAnalysis"]["feedback"],
                "feedback": parsed_ai_feedback.get("experienceEvaluation", {}).get("feedback", "")
            },
            
            "educationEvaluation": {
                "hasInstitution": deterministic_report["hygieneCheck"]["hasInstitution"],
                "hasDuration": deterministic_report["hygieneCheck"]["hasDuration"],
                "hasScore": deterministic_report["hygieneCheck"]["hasScore"],
                "rating": deterministic_report["overallRating"],
                "feedback": parsed_ai_feedback.get("educationEvaluation", {}).get("educationExplanation", "")
            },
            
            # FIXED: Seamlessly inject isolated, case-by-case textual analysis for each project
            "projectEvaluation": [
                {
                    "projectName": proj.get("projectName", "Unnamed Project"),
                    "rating": proj.get("rating", "Average"),
                    "projectUrl": proj.get("projectUrl", ""),
                    "feedback": next(
                        (ai_proj.get("feedback") for ai_proj in ai_project_feedback 
                         if str(ai_proj.get("projectName", "")).lower() == str(proj.get("projectName", "")).lower()),
                        f"Project evaluated deterministically at a rubric score of {proj.get('score')}/40. Review deployment channels and impact metrics."
                    )
                }
                for proj in python_projects
            ],
            
            "suggestedProject": parsed_ai_feedback.get("suggestedProject", {}),
            "finalRecommendations": parsed_ai_feedback.get("finalRecommendations", []),
            "hygieneCheck": deterministic_report["hygieneCheck"]
        }

        # Sync text grammar issues directly into output node payload
        final_response["hygieneCheck"]["grammarIssues"] = parsed_ai_feedback.get("hygieneCheck", {}).get("grammarIssues", [])
        return final_response
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail="Failed to parse AI response",
        )
    except HTTPException:
        # Re-raise internal HTTPExceptions cleanly without wiping contexts
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"ATS analysis failed: {str(e)}",
        )