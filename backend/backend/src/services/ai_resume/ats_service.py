import json
import uuid

from fastapi import HTTPException
from openai import AsyncOpenAI

from src.config.manager import settings
from src.utilities.link_validator import SmartLinkValidator
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
        # 1. Run our real-time network Link Validator Pipeline first (Async/Non-blocking)
        link_validator = SmartLinkValidator()
        verified_links_context = await link_validator.validate_all_links_async(resume_text)
        
        # Format a summary context string to feed directly into OpenAI
        link_report_summary = (
            f"Total Links Found: {verified_links_context['total_links_found']}, "
            f"Working/Valid: {verified_links_context['summary']['working']}, "
            f"Broken/Invalid: {verified_links_context['summary']['broken']}. "
            f"Detailed Records: {json.dumps(verified_links_context['links'])}"
        )

        # 2. Inject this verified link context directly into our resume text block
        enhanced_resume_input = f"""
{resume_text}
        
----- PROGRAMMATIC NETWORK LINK VALIDATION REPORT -----
{link_report_summary}
        """

        # 3. Build the comprehensive structured prompt using our dynamic matrices
        # Build prompt
        prompt = build_ats_analysis_prompt(
            resume_text=enhanced_resume_input,
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
        # 5. Production Clean-up: Whitespace trimming for strings inside skills lists
        if "skillsAnalysis" in parsed_response:
            sa = parsed_response["skillsAnalysis"]
            sa["strongSkills"] = [str(s).strip() for s in sa.get("strongSkills", [])]
            sa["missingSkills"] = [str(s).strip() for s in sa.get("missingSkills", [])]
            sa["deprioritizedSkills"] = [str(s).strip() for s in sa.get("deprioritizedSkills", [])]
        # 6. Safe Layout Remapping Layer for Frontend Cards (Figma 1:1 sync)
        if "scoreBreakdown" in parsed_response:
            sb = parsed_response["scoreBreakdown"]
            
            # Penalize the layout/formatting tracking card heavily if they have links but all are broken
            formatting_final_score = sb.get("formattingScore", 80)
            if verified_links_context['total_links_found'] > 0 and verified_links_context['summary']['working'] == 0:
                formatting_final_score = min(formatting_final_score, 40)

            # Restructure JSON node keys to map perfectly to your 4 React dashboard cards
            parsed_response["scoreBreakdown"] = {
                # "skillsMatch": sb.get("skillsMatch", 70),
                # "experience": sb.get("experienceMatch", 70),  # Handles fresher/project mapping automatically
                # "formatting": formatting_final_score,
                # "keywords": sb.get("keywordDensity", sb.get("educationValidation", 70))
                "skillsMatch": sb.get("skillsMatch", 70),
                "experienceMatch": sb.get("experienceMatch", 70),
                "formattingScore": formatting_final_score,
                "keywordDensity": sb.get("keywordDensity", sb.get("educationValidation", 70))
            }



        # 7. DESIGNER VS TECH TRACK SAFETY HANDLER
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
    except HTTPException:
        # Re-raise internal HTTPExceptions cleanly without wiping contexts
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"ATS analysis failed: {str(e)}",
        )