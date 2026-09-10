import json
import uuid
import traceback
from typing import Any
from fastapi import HTTPException
from openai import AsyncOpenAI

from src.config.manager import settings
from src.utilities.link_validator import SmartLinkValidator
from src.services.ai_resume.scoring.ats_engine import ATSEngine
from src.services.ai_resume.scoring.project_mapper import ProjectLinkMapper
from src.services.ai_resume.prompt_builder import (
    build_ats_analysis_prompt,
)

# Initialize OpenAI client
client = AsyncOpenAI(
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_API_BASE,
)

ats_engine = ATSEngine()
project_mapper = ProjectLinkMapper()


async def generate_ats_analysis(
    resume_payload: Any,  
    target_role: str,
    experience_level: str,
    job_description: str,
    parsed_resume_json: dict,
):
    """
    Executes the production ATS Assessment Pipeline:
    1. Extracts spatial and embedded links directly from structured parser payload.
    2. Runs SmartLinkValidator to classify network integrity and platform types.
    3. Executes multi-signal ProjectLinkMapper to bind URLs accurately.
    4. Computes 100% deterministic Python scoring matrices.
    5. Ingests OpenAI LLM natural language feedback narratives.
    """
    try:
        print("\n================ [ATS SERVICE DEBUG START] ================")
        print(f"Target Role: {target_role} | Experience Input Level: {experience_level}")

        # Handle both structured parser response dict and fallback raw text
        if isinstance(resume_payload, dict):
            resume_text = resume_payload.get("text", "")
            document_map = resume_payload.get("documentMap", [])
            embedded_links = resume_payload.get("embeddedLinks", [])
        else:
            resume_text = str(resume_payload)
            document_map = []
            embedded_links = []

        # 1. Async network validation & platform classification
        link_validator = SmartLinkValidator()
        # Ingest pre-extracted spatial links or fallback text buffer
        extracted_targets = embedded_links if embedded_links else []
        verified_links_context = await link_validator.validate_all_links_async(extracted_targets)

        print("\n--- [STEP 1: SmartLinkValidator Output Links] ---")
        print(json.dumps(verified_links_context, indent=2))

        # 2. Extract structures from upstream parsed resume object
        parsed_skills = parsed_resume_json.get("skills", [])
        print(f"Parsed Skills Extracted {(parsed_skills)}")
        parsed_education = parsed_resume_json.get("education", [])
        parsed_projects = parsed_resume_json.get("projects", [])
        parsed_experience = parsed_resume_json.get("experience", [])
        print("\n====== EXPERIENCE EXTRACTION ======")
        print(f"Experience Records: {len(parsed_experience)}")
        print(f"Parsed Experience Records: {json.dumps(parsed_experience, indent=2)}")

        jd_skills_extracted = parsed_resume_json.get("target_jd_skills", [])
        

        print("\n--- [STEP 2: Upstream Parsed Project Nodes Count] ---")
        print(f"Found {len(parsed_projects)} structural projects in parsed JSON.")

        # 3. Multi-Signal Project Link Mapping
        mapped_projects = project_mapper.map_links_to_projects(
            projects=parsed_projects,
            validated_links=verified_links_context,
            document_map=document_map
        )

        print("\n--- [STEP 3: Final Bound Projects State Before Scoring] ---")
        for p in mapped_projects:
            print(f"Project: {p.get('title', p.get('projectName'))} -> URL: '{p.get('projectUrl', '')}'")

        # 4. Execute Deterministic Scoring Engine
        deterministic_report = ats_engine.run_assessment(
            verified_links_raw=verified_links_context,
            parsed_skills=parsed_skills,
            jd_skills=jd_skills_extracted,
            parsed_education=parsed_education,
            parsed_projects=mapped_projects,
            parsed_experience=parsed_experience,
            experience_level=experience_level,
            target_role=target_role,
            raw_resume_text=resume_text,
            raw_jd_text=job_description
        )

        print("\n--- [STEP 4: Deterministic Engine Report Snapshot] ---")
        print(f"Master score computed: {deterministic_report['atsScore']}")
        print(f"Hygiene check snapshot: {json.dumps(deterministic_report['hygieneCheck'], indent=2)}")

        # 5. Build structured prompt and call OpenAI LLM
        prompt = build_ats_analysis_prompt(
            resume_text=resume_text,
            deterministic_report=deterministic_report,
            target_role=target_role,
            experience_level=experience_level,
            job_description=job_description,
        )

        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            # temperature=0.3,
            temperature=1,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional ATS resume evaluator. Use your natural language capabilities "
                        "exclusively to explain provided scores. Output only raw JSON formats."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )

        ai_response = response.choices[0].message.content
        if not ai_response:
            raise HTTPException(
                status_code=500,
                detail="Empty AI response received",
            )

        parsed_ai_feedback = json.loads(ai_response)

        python_projects = deterministic_report.get("deterministicMetrics", {}).get("projectModule", {}).get("projectEvaluation", [])
        ai_project_feedback = parsed_ai_feedback.get("projectEvaluation", [])

        # 6. Final Composite Fusion
        final_response = {
            "analysisId": str(uuid.uuid4()),
            "atsScore": deterministic_report["atsScore"],
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
                "feedback": parsed_ai_feedback.get("educationEvaluation", {}).get("educationExplanation", "")
            },
            "projectEvaluation": [
                {
                    "projectName": proj.get("projectName", "Unnamed Project"),
                    "rating": proj.get("rating", "Average"),
                    "projectUrl": proj.get("projectUrl", ""),
                    "feedback": next(
                        (ai_proj.get("feedback") for ai_proj in ai_project_feedback 
                         if str(ai_proj.get("projectName", "")).lower() == str(proj.get("projectName", "")).lower()),
                        f"Project evaluated deterministically at a rubric score of {proj.get('score')}/35."
                    )
                }
                for proj in python_projects
            ],
            "suggestedProject": parsed_ai_feedback.get("suggestedProject", {}),
            "finalRecommendations": parsed_ai_feedback.get("finalRecommendations", []),
            "hygieneCheck": deterministic_report["hygieneCheck"]
        }

        final_response["hygieneCheck"]["grammarIssues"] = parsed_ai_feedback.get("hygieneCheck", {}).get("grammarIssues", [])
        print("================ [ATS SERVICE DEBUG END] ================\n")
        return final_response

    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail="Failed to parse AI response",
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"ATS analysis failed: {str(e)}",
        )