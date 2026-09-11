"""Service for analyzing structure practice answers using LLM."""

import logging
from pydantic import BaseModel, Field
from src.services.llm import structured_output

logger = logging.getLogger(__name__)


class FrameworkSectionAnalysis(BaseModel):
    """Analysis for a single framework section."""
    name: str
    present: bool
    quality: str  # "good", "partial", "missing"
    time_estimate_seconds: int = Field(..., ge=0, le=300)


class StructureAnalysisResult(BaseModel):
    """Structured LLM output for structure analysis."""
    framework_detected: str  # e.g., "C-T-E-T-D", "STAR", "Custom"
    sections: list[FrameworkSectionAnalysis]
    completion_percentage: int = Field(..., ge=0, le=100)
    key_insight: str
    progress_message: str


async def analyze_structure_answer(
    *,
    question_text: str,
    structure_hint: str,
    answer_text: str,
    framework: str = None,
    submitted_sections: dict = None,
    expected_sections: list[str] = None,
) -> tuple[StructureAnalysisResult | None, str | None, int, str | None]:
    """
    Analyze a structure practice answer using LLM.
    
    Args:
        question_text: The question being answered
        structure_hint: The structure hint provided to the user
        answer_text: The user's answer to analyze (combined from sections)
        framework: Framework type (STAR, C-T-E-T-D, GCDIO)
        submitted_sections: Dict mapping section_name -> {answer_text, time_spent_seconds, submitted}
        expected_sections: List of expected section names for this framework
    
    Returns:
        Tuple of (analysis_result, error_message, latency_ms, llm_model)
    """
    # Determine framework from parameter or hint
    if not framework:
        framework = "C-T-E-T-D"  # Default
        if "DIRECT" in structure_hint.upper():
            framework = "DIRECT"
        elif "STAR" in structure_hint.upper():
            framework = "STAR"
        elif "C-T-E-T-D" in structure_hint.upper():
            framework = "C-T-E-T-D"
        elif "GCDIO" in structure_hint.upper() or "G-C-D-I-O" in structure_hint.upper():
            framework = "GCDIO"
    
    # Build section information for prompt
    sections_info = ""
    if submitted_sections and expected_sections:
        sections_info = "\n\nSections submitted by user:\n"
        for section in expected_sections:
            if section in submitted_sections:
                time_spent = submitted_sections[section].get('time_spent_seconds', 0)
                sections_info += f"- {section}: SUBMITTED ({time_spent}s)\n"
            else:
                sections_info += f"- {section}: NOT SUBMITTED\n"
    
    # Build analysis prompt
    system_prompt = f"""You are an expert interview coach analyzing structured answers.
Analyze the answer based on the {framework} framework.

For C-T-E-T-D:
- Context: Background and setup
- Theory: Conceptual explanation
- Example: Concrete example with details
- Trade-offs: Pros/cons or alternatives
- Decision: Conclusion or recommendation

For STAR:
- Situation: Context and background
- Task: What needed to be done
- Action: Steps taken
- Result: Outcome and impact

For GCDIO:
- Goal: Objective or problem statement
- Constraints: Limitations or requirements
- Decision: Chosen approach or solution
- Implementation: How it was executed
- Outcome: Results and impact

For DIRECT (purely factual/definitional questions with no structured framework):
- Answer: A single section — the candidate's direct explanation of the concept

The user submitted answers section-by-section. Each section is marked with [Section Name].{sections_info}

Analyze each section that was submitted:
- "good": Well-developed, clear, specific, addresses the section requirements
- "partial": Present but underdeveloped, rushed, or incomplete
- "missing": Not submitted or not addressed

If the framework is DIRECT, grade the single "Answer" section only for correctness and clarity of the explanation — do NOT penalize it for lacking structural elements (context, trade-offs, results, etc.) that a factual/definitional question was never expected to have.

For submitted sections, use their actual recorded time. For missing sections, estimate 0.
Provide a key insight about what the candidate did well and what could be improved.
Calculate completion percentage (0-100) based on:
- How many sections were submitted (weight: 50%)
- Quality of submitted sections (weight: 50%)

Return ONLY valid JSON matching this structure:
{{
  "framework_detected": "{framework}",
  "sections": [
    {{
      "name": "section name",
      "present": true/false,
      "quality": "good"/"partial"/"missing",
      "time_estimate_seconds": integer (0-300)
    }}
  ],
  "completion_percentage": integer (0-100),
  "key_insight": "detailed insight string",
  "progress_message": "encouraging message based on completion"
}}"""

    user_content = {
        "question": question_text,
        "structure_hint": structure_hint,
        "answer": answer_text,
        "framework": framework,
        "sections_submitted": len(submitted_sections) if submitted_sections else 0,
        "expected_sections": expected_sections or [],
        "instructions": [
            f"Analyze based on {framework} framework sections",
            "User submitted answers section-by-section, each marked with [Section Name]",
            "Assess each SUBMITTED section for quality (good/partial)",
            "Mark non-submitted sections as 'missing' with quality='missing'",
            "Use actual recorded time for submitted sections",
            "Calculate completion percentage: 50% for sections submitted, 50% for quality",
            "Provide specific insight on what was done well and what needs improvement"
        ]
    }

    # Use structured_output helper from llm.py
    result, error, latency_ms, model = await structured_output(
        StructureAnalysisResult,
        system_prompt=system_prompt,
        user_content=user_content,
        temperature=0.3,
    )
    
    if result:
        logger.info(f"Structure analysis completed: {framework}, completion={result.completion_percentage}%, latency={latency_ms}ms")
    
    return result, error, latency_ms, model


def _estimate_section_time(answer_text: str, section_text: str) -> int:
    """
    Estimate time spent on a section based on word count and complexity.
    Simple heuristic: ~30 words per minute for thoughtful writing.
    """
    if not section_text:
        return 0
    
    word_count = len(section_text.split())
    # Assume 30 words/minute = 0.5 words/second = 2 seconds/word
    estimated_seconds = int(word_count * 2)
    
    # Cap at reasonable limits
    return min(max(estimated_seconds, 5), 120)
