"""Small AI helpers for staff writing: rephrase a note, draft interview questions.

Both return suggestions only. Nothing is saved here; the person reads the text, edits it if
needed, and saves it through the normal form. When the AI is unavailable the caller gets None
and tells the person to keep writing by hand.
"""

import pydantic

from src.services.llm import structured_output

PURPOSES = {
    "learner_feedback": (
        "feedback that a student will read after an interview. Warm, specific and encouraging. "
        "Say what went well, then what to improve and how. Speak to the student as 'you'."
    ),
    "interview_note": (
        "a short internal note about how an interview went (who joined, what happened). "
        "Factual and neutral, one or two sentences."
    ),
    "private_note": (
        "private notes for colleagues who review an interview. Factual, specific, no judgement "
        "about the person's character."
    ),
    "handoff_note": (
        "a recommendation from the Program team to the Placement team about a candidate. "
        "Professional and specific: strengths, evidence, and the roles that fit."
    ),
    "answer_remark": (
        "the interviewer's remark on one answer in an interview. Short, specific, evidence based."
    ),
    "general": "a clear, professional message.",
}


class Rephrased(pydantic.BaseModel):
    text: str


class SheetQuestion(pydantic.BaseModel):
    text: str
    area: str | None = None


class Sheet(pydantic.BaseModel):
    questions: list[SheetQuestion]


async def rephrase(text: str, purpose: str) -> str | None:
    goal = PURPOSES.get(purpose, PURPOSES["general"])
    result, *_ = await structured_output(
        Rephrased,
        system_prompt=(
            "You rewrite text written by staff at an Indian skilling programme. The text is "
            f"{goal} Keep every fact, name, number and judgement exactly as given; never add new "
            "facts. Fix grammar and spelling, make it clear and kind, and keep about the same "
            "length. Use simple English. Reply as JSON: {\"text\": \"...\"}."
        ),
        user_content=text,
        temperature=0.3,
        max_tokens=1200,
    )
    if result is None or not result.text.strip():
        return None
    return result.text.strip()


async def question_sheet(
    *, title: str, role: str | None, notes: str | None, focus: str | None, count: int
) -> list[dict] | None:
    result, *_ = await structured_output(
        Sheet,
        system_prompt=(
            "You prepare questions for a live interview that a human interviewer will ask a "
            "fresher from an Indian skilling programme. Write clear, open questions, one idea "
            "each, ordered from easy to hard, covering different areas. Give each a short area "
            "label such as 'Communication', 'Problem solving', 'Technical' or 'Projects'. "
            'Reply as JSON: {"questions": [{"text": "...", "area": "..."}]}.'
        ),
        user_content={
            "round": title,
            "job_role": role,
            "what_the_learner_was_asked_to_prepare": notes,
            "focus_from_interviewer": focus,
            "number_of_questions": count,
        },
        temperature=0.5,
        max_tokens=2000,
    )
    if result is None:
        return None
    out = []
    for q in result.questions:
        text = q.text.strip()
        if 5 <= len(text) <= 500:
            out.append({"text": text, "area": (q.area or "").strip()[:60] or None})
    return out[:count] or None
