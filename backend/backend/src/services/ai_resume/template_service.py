import json
from fastapi import HTTPException
from openai import AsyncOpenAI

from src.config.manager import settings
from src.services.ai_resume.prompt_builder import build_structuring_prompt

# Initialize OpenAI client
client = AsyncOpenAI(
    api_key=settings.OPENAI_API_KEY,
)

async def generate_structured_resume_data(
    resume_text: str,
    analysis_result: dict,
):
    """
    Takes raw resume text and an ATS analysis, and uses OpenAI to output
    a fully structured JSON dictionary matching the resume templates schema.
    """
    try:
        # Build prompt
        prompt = build_structuring_prompt(
            resume_text=resume_text,
            analysis_result=analysis_result,
        )

        # Call OpenAI
        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional resume formatter. Output only valid JSON."
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
                detail="Empty AI structuring response received",
            )

        # Convert JSON string to Python dict
        parsed_response = json.loads(ai_response)

        return parsed_response

    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail="Failed to parse structured resume JSON",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Resume structuring failed: {str(e)}",
        )
