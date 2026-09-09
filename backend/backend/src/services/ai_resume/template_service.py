import json
from fastapi import HTTPException

from src.services.llm import get_llm_client, get_active_llm_model_and_key
from src.services.ai_resume.prompt_builder import build_structuring_prompt

async def generate_structured_resume_data(
    resume_text: str,
    analysis_result: dict,
):
    """
    Takes raw resume text and an ATS analysis, and uses the configured LLM
    provider to output a fully structured JSON dictionary matching the
    resume templates schema.
    """
    try:
        # Build prompt
        prompt = build_structuring_prompt(
            resume_text=resume_text,
            analysis_result=analysis_result,
        )

        client = get_llm_client()
        model, _ = get_active_llm_model_and_key()
        if client is None:
            raise HTTPException(
                status_code=503,
                detail="LLM provider is not configured",
            )

        response = await client.chat.completions.create(
            model=model,
            temperature=1,
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
