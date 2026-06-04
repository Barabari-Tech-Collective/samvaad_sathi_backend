import json
import uuid

from fastapi import HTTPException
from openai import AsyncOpenAI

from src.config.manager import settings
from src.services.ai_resume.prompt_builder import (
    build_ats_analysis_prompt,
)

# Initialize OpenAI client
client = AsyncOpenAI(
    api_key=settings.OPENAI_API_KEY,
)


async def generate_ats_analysis(
    resume_text: str,
    target_role: str,
    experience_level: str,
    job_description: str,
):
    """
    Generates ATS analysis using OpenAI.
    """

    try:
        # Build prompt
        prompt = build_ats_analysis_prompt(
            resume_text=resume_text,
            target_role=target_role,
            experience_level=experience_level,
            job_description=job_description,
        )

        # Call OpenAI
        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional ATS resume evaluator."
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
        parsed_response = json.loads(ai_response)
        # LOGICAL PATCH (DESIGNER VS TECH TRACK SAFETY HANDLER)

        if "skillsAnalysis" in parsed_response:
            sa = parsed_response["skillsAnalysis"]
            sa["strongSkills"] = [str(s).strip() for s in sa.get("strongSkills", [])]
            sa["missingSkills"] = [str(s).strip() for s in sa.get("missingSkills", [])]
            sa["deprioritizedSkills"] = [str(s).strip() for s in sa.get("deprioritizedSkills", [])]

        # If it's a UI/UX/Product/Graphic design role, force bypass Github checks
        is_design_track = any(k in target_role.lower() for k in ["design", "ui", "ux", "graphics", "product designer"])
        if is_design_track and "hygieneCheck" in parsed_response:
            # Force true so that the frontend checklist shows a green check instead of an error icon
            parsed_response["hygieneCheck"]["hasGithub"] = True

        # Add analysisId
        parsed_response["analysisId"] = str(uuid.uuid4())

        return parsed_response

    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail="Failed to parse AI response",
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"ATS analysis failed: {str(e)}",
        )