"""Service for generating structure hints for interview questions."""

import logging
from typing import Any

from pydantic import BaseModel
from src.services.llm import structured_output

logger = logging.getLogger(__name__)


class StructureHint(BaseModel):
    """Single structure hint for a question."""
    question_number: int
    hint: str


class StructureHintsResponse(BaseModel):
    """Response containing all structure hints."""
    hints: list[StructureHint]


def _get_client():
    """Deprecated - using structured_output helper instead."""
    return None


async def generate_structure_hints_for_questions(
    questions: list[dict[str, Any]],
    track: str,
    difficulty: str,
) -> tuple[dict[str, str], str | None, int, str]:
    """
    Generate structure hints for interview questions.
    
    Args:
        questions: List of question dicts with 'text', 'topic', 'category' keys
        track: Interview track (e.g., 'data_science', 'frontend')
        difficulty: Interview difficulty level
    
    Returns:
        Tuple of (hints_map, error, latency_ms, model):
        - hints_map: Dict mapping question text to structure hint
        - error: Error message if failed, None if successful
        - latency_ms: Time taken for LLM call
        - model: Model name used
    """
    if not questions:
        return {}, None, 0, "none"
    
    # Build the prompt
    questions_list = []
    for i, q in enumerate(questions, 1):
        text = q.get("text", "")
        topic = q.get("topic", "")
        category = q.get("category", "technical")
        questions_list.append(f"{i}. {text} [Topic: {topic}, Category: {category}]")
    
    questions_text = "\n".join(questions_list)
    
    system_prompt = """You are an expert interview coach helping candidates prepare for technical interviews.
Your task is to provide brief structure hints (1-2 lines) that guide candidates on how to structure their answers effectively.

Use these proven frameworks based on question type:

**Tech Questions** - Use C-T-E-T-D Framework:
- Context → Theory → Example → Trade-offs → Decision
- Example hint: "Start with context, explain the underlying theory, give a concrete example, discuss trade-offs, then justify your decision."

**Tech Allied Questions** - Use G-C-D-I-O Framework:
- Goal → Constraints → Decision → Implementation → Outcome
- Example hint: "Outline the goal first, identify constraints, explain your decision rationale, describe implementation, and summarize the outcome."

**Behavioral Questions** - Use S-T-A-R Framework:
- Situation → Task → Action → Result
- Example hint: "Use STAR: describe the Situation, clarify your Task, detail the Actions you took, and quantify the Results achieved."

**Purely Factual or Definitional Questions** (e.g. "What is X?", "Is A related to B?", "Are X and Y the same thing?") - Use NO framework:
- These questions ask the candidate to explain or define something, not to tell a structured story or walk through a design process.
- Do not force them into C-T-E-T-D, G-C-D-I-O, or STAR — those frameworks don't fit a plain definition and make the practice feel nonsensical.
- Instead, produce a hint that starts with the literal word "DIRECT" followed by a colon, e.g. "DIRECT: Just explain clearly and concisely what it is — no need for a multi-part structure."

The hints should:
- Match the appropriate framework to the question type — and use DIRECT when no framework genuinely fits
- Be concise (max 2 lines)
- Focus on structure, NOT content
- Guide candidates on organizing their thoughts
- NOT give away the answer

You must respond with valid JSON matching this schema:
{
  "hints": [
    {"question_number": 1, "hint": "..."},
    {"question_number": 2, "hint": "..."}
  ]
}
"""

    user_prompt = f"""Interview Track: {track}
Difficulty: {difficulty}

Questions:
{questions_text}

For each question above, provide a structure hint."""

    # Use the structured_output helper
    parsed_response, error, latency_ms, model_name = await structured_output(
        StructureHintsResponse,
        system_prompt=system_prompt,
        user_content=user_prompt,
        temperature=0.7,
    )
    
    if error or not parsed_response:
        logger.warning(f"Failed to generate structure hints with LLM: {error}")
        return _generate_fallback_hints(questions), error, latency_ms or 0, model_name
    
    # Map hints to questions
    hints_map = {}
    for i, q in enumerate(questions, 1):
        hint_obj = next((h for h in parsed_response.hints if h.question_number == i), None)
        if hint_obj:
            hints_map[q.get("text", "")] = hint_obj.hint
        else:
            hints_map[q.get("text", "")] = _get_fallback_hint_for_question(q)
    
    return hints_map, None, latency_ms or 0, model_name


def _generate_fallback_hints(questions: list[dict[str, Any]]) -> dict[str, str]:
    """Generate fallback hints when LLM is unavailable."""
    hints_map = {}
    for q in questions:
        hints_map[q.get("text", "")] = _get_fallback_hint_for_question(q)
    return hints_map


def _get_fallback_hint_for_question(question: dict[str, Any]) -> str:
    """Generate a fallback hint for a single question based on category."""
    category = (question.get("category") or "technical").lower()
    text = (question.get("text") or "").strip().lower()

    # Plain "What is X?" / "Is A related to B?" style questions don't fit a
    # structured story or design-process framework — treat as direct-answer.
    _factual_starters = ("what is ", "what are ", "what does ", "define ", "is ", "are ")
    if text.startswith(_factual_starters):
        return "DIRECT: Just explain clearly and concisely what it is — no need for a multi-part structure."

    if "behavioral" in category:
        return "Use STAR: Situation → Task → Action → Result. Focus on your specific role and measurable outcomes."
    elif "system" in category or "design" in category or "architecture" in category:
        return "Apply G-C-D-I-O: Goal → Constraints → Decision → Implementation → Outcome. Start with requirements and constraints."
    elif "algorithm" in category or "coding" in category:
        return "Follow C-T-E-T-D: Context → Theory → Example → Trade-offs → Decision. Clarify assumptions, explain approach, discuss complexity."
    else:
        # Default tech question
        return "Use C-T-E-T-D: Context → Theory → Example → Trade-offs → Decision. Build from fundamentals to practical application."
