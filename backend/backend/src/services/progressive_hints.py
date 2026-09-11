"""Service for generating progressive section hints for structure practice."""

import re
from typing import Dict, List, Literal

# Framework definitions
FRAMEWORKS = {
    "C-T-E-T-D": {
        "name": "C-T-E-T-D",
        "sections": ["Context", "Theory", "Example", "Trade-offs", "Decision"],
        "base_hints": {
            "Context": "Set the stage. Explain the background, scenario, or environment where this concept applies.",
            "Theory": "Define the core concept. Explain how it works, key principles, or the underlying mechanism.",
            "Example": "Provide a concrete example. Show working code, a real scenario, or a practical demonstration.",
            "Trade-offs": "Discuss pros and cons. What are the benefits? What are the limitations or downsides?",
            "Decision": "Conclude with your recommendation. When would you use this? What's your best practice?",
        }
    },
    "STAR": {
        "name": "STAR",
        "sections": ["Situation", "Task", "Action", "Result"],
        "base_hints": {
            "Situation": "Describe the context. What was happening? Where were you? What was the challenge or scenario?",
            "Task": "Define your responsibility. What was your specific role? What were you asked to do?",
            "Action": "Explain your actions. What specific steps did you take? How did you approach the problem?",
            "Result": "Share the outcome. What happened? What were the measurable results? What did you learn?",
        }
    },
    "GCDIO": {
        "name": "G-C-D-I-O",
        "sections": ["Goal", "Constraints", "Decision", "Implementation", "Outcome"],
        "base_hints": {
            "Goal": "State the objective. What were you trying to achieve? What problem needed solving?",
            "Constraints": "Identify limitations. What constraints did you face? (time, resources, requirements, etc.)",
            "Decision": "Explain your choice. What approach did you decide on? Why did you choose it over alternatives?",
            "Implementation": "Describe execution. How did you implement your decision? What specific steps or code?",
            "Outcome": "Share results. What was the impact? Did you meet the goal? What were the trade-offs?",
        }
    },
    "DIRECT": {
        "name": "Direct Answer",
        "sections": ["Answer"],
        "base_hints": {
            "Answer": "Answer clearly and directly. This is a factual question — no need to follow a multi-part structure, just explain your understanding.",
        }
    },
}


def detect_framework(structure_hint: str) -> str:
    """
    Detect which framework to use based on the structure hint.

    Args:
        structure_hint: The structure hint from the question

    Returns:
        Framework name: "C-T-E-T-D", "STAR", "GCDIO", or "DIRECT"
    """
    hint_lower = structure_hint.lower()

    def _has_word(word: str) -> bool:
        # Word-boundary match so e.g. "Start with context" doesn't false-positive
        # on the substring "star".
        return re.search(rf"\b{re.escape(word)}\b", hint_lower) is not None

    if _has_word("direct"):
        return "DIRECT"
    elif _has_word("star") or _has_word("situation"):
        return "STAR"
    elif _has_word("gcdio") or _has_word("g-c-d-i-o") or (_has_word("goal") and _has_word("constraints")):
        return "GCDIO"
    else:
        # Default to C-T-E-T-D for technical questions
        return "C-T-E-T-D"


def get_framework_sections(framework: str) -> List[str]:
    """Get the list of sections for a framework."""
    return FRAMEWORKS.get(framework, FRAMEWORKS["C-T-E-T-D"])["sections"]


def get_initial_hint(framework: str) -> Dict[str, str]:
    """
    Get the initial hint for starting the first section.
    
    Returns:
        Dict with section_name and hint
    """
    fw_data = FRAMEWORKS.get(framework, FRAMEWORKS["C-T-E-T-D"])
    first_section = fw_data["sections"][0]
    
    return {
        "section_name": first_section,
        "hint": f"Start with {first_section}: {fw_data['base_hints'][first_section]}",
        "framework": framework,
        "total_sections": len(fw_data["sections"]),
    }


def get_next_section_hint(
    framework: str,
    current_section: str,
    previous_answer: str | None = None,
) -> Dict[str, str] | None:
    """
    Generate a hint for the next section based on the current section and answer.
    
    Args:
        framework: Framework name (C-T-E-T-D, STAR, GCDIO)
        current_section: The section that was just completed
        previous_answer: The answer text for the current section (optional, for context)
        
    Returns:
        Dict with next section info, or None if all sections complete
    """
    fw_data = FRAMEWORKS.get(framework, FRAMEWORKS["C-T-E-T-D"])
    sections = fw_data["sections"]
    
    try:
        current_idx = sections.index(current_section)
    except ValueError:
        # Invalid section name, return first section
        return {
            "section_name": sections[0],
            "hint": fw_data["base_hints"][sections[0]],
        }
    
    # Check if this was the last section
    if current_idx >= len(sections) - 1:
        return None  # All sections complete
    
    # Get next section
    next_section = sections[current_idx + 1]
    base_hint = fw_data["base_hints"][next_section]
    
    # Add contextual encouragement based on progress
    progress_messages = {
        0: f"Great start! Now move to {next_section}. ",
        1: f"Good progress! Next is {next_section}. ",
        2: f"You're halfway there! Now explain {next_section}. ",
        3: f"Almost done! Time for {next_section}. ",
    }
    
    encouragement = progress_messages.get(current_idx, f"Continue with {next_section}. ")
    
    return {
        "section_name": next_section,
        "hint": encouragement + base_hint,
        "sections_complete": current_idx + 1,
        "total_sections": len(sections),
    }


def get_completion_message(framework: str) -> str:
    """Get message when all sections are complete."""
    return f"Excellent! You've completed all sections of the {framework} framework. Your answer will now be analyzed."


def get_framework_info(framework: str) -> Dict:
    """Get complete info about a framework."""
    fw_data = FRAMEWORKS.get(framework, FRAMEWORKS["C-T-E-T-D"])
    return {
        "name": fw_data["name"],
        "sections": fw_data["sections"],
        "total_sections": len(fw_data["sections"]),
        "hints": fw_data["base_hints"],
    }
