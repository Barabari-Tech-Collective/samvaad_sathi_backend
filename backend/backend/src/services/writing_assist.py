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


PLAN_MODULES = ("warm_up", "think_fast", "pronunciation", "structure", "pace")


class PlanStepDraft(pydantic.BaseModel):
    kind: str
    title: str
    instructions: str | None = None
    module: str | None = None


class PlanDraft(pydantic.BaseModel):
    title: str
    steps: list[PlanStepDraft]


async def plan_draft(*, goal: str, weeks: int) -> dict | None:
    """A short learning plan made only from the drills and reflections the product really has."""
    result, *_ = await structured_output(
        PlanDraft,
        system_prompt=(
            "You help a trainer at an Indian skilling programme plan interview preparation for freshers. "
            "Write a short plan of 3 to 7 steps that moves from easy to harder. Each step is either a "
            "'drill' or a 'reflection'. A drill step must set module to exactly one of: "
            "warm_up (easy relaxed chat to start speaking), think_fast (quick answers to surprise "
            "questions), pronunciation (say difficult words clearly), structure (give answers a clear "
            "start, middle and end), pace (speak at a steady speed with pauses). A reflection step has "
            "module null and asks the student to write what they will say differently. Titles are short "
            "and plain, written to the student. Instructions are one or two simple sentences. "
            'Reply as JSON: {"title": "...", "steps": [{"kind": "drill|reflection", "title": "...", '
            '"instructions": "...", "module": "..."|null}]}.'
        ),
        user_content={"goal": goal, "plan_length_weeks": weeks},
        temperature=0.4,
        max_tokens=1500,
    )
    if result is None:
        return None
    steps = []
    for step in result.steps:
        title = (step.title or "").strip()[:160]
        if len(title) < 3:
            continue
        kind = step.kind if step.kind in ("drill", "reflection") else None
        if kind == "drill" and step.module not in PLAN_MODULES:
            continue
        steps.append(
            {
                "kind": kind or "reflection",
                "title": title,
                "instructions": ((step.instructions or "").strip()[:2000] or None),
                "module": step.module if kind == "drill" else None,
            }
        )
    title = (result.title or "").strip()[:160]
    if len(steps) < 2 or len(title) < 3:
        return None
    return {"title": title, "steps": steps[:7]}
