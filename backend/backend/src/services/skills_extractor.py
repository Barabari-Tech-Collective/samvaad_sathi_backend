from typing import List, Tuple
from src.services.llm import extract_jd_skills_with_llm

async def extract_skills_from_text(text: str) -> Tuple[List[str], str | None]:
    """
    Extracts skills using an LLM.
    Returns a tuple of (skills_list, error_message).
    """
    if not text:
        return [], None
    
    skills, error = await extract_jd_skills_with_llm(text)
    return skills, error
