"""Immutable fixed-length sitting plans. Legacy attempts have no plan.

Version 1 deliberately reserves no adaptive slots: every prompt is a base
question and a follow-up writer cannot append beyond the declared total.
"""

import copy
import re

import fastapi


def configured_count(value) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= 50:
        raise ValueError("Questions per interview must be an integer from 1 to 50")
    return value


def planned_count(interview) -> int | None:
    plan = getattr(interview, "question_plan", None)
    if not isinstance(plan, dict):
        return None
    total = configured_count(plan.get("total"))
    if plan.get("version") != 1 or total is None or plan.get("adaptive_slots", 0) != 0:
        raise ValueError("Unsupported or incomplete fixed question plan")
    return total


def unique_questions(questions):
    seen = set()
    selected = []
    for question in questions:
        text = getattr(question, "question_text", "")
        if not isinstance(text, str) or not text.strip():
            continue
        normalized = re.sub(r"\s+", " ", text).strip().casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        selected.append(question)
    return selected


def make_curated_plan(
    profile, *, difficulty: str, offset: int = 0, default_count: int | None = None
) -> dict | None:
    from src.services.interview_eligibility import level_for_difficulty

    explicit = getattr(profile, "questions_per_interview", None)
    total = configured_count(explicit if explicit is not None else default_count)
    if total is None:
        return None
    level = level_for_difficulty(difficulty)
    pool = unique_questions(
        q for q in sorted(profile.questions, key=lambda q: q.id) if q.level == level
    )
    if len(pool) < total:
        raise fastapi.HTTPException(
            409,
            f"This level needs {total} unique approved questions before an interview can start.",
        )
    selected = [pool[(offset + i) % len(pool)] for i in range(total)]
    return {
        "version": 1,
        "total": total,
        "adaptive_slots": 0,
        "duration_seconds": total * 180,
        "interviewer_mode": "ai",
        "source_profile_id": profile.id,
        "questions": [
            {
                "text": q.question_text.strip(),
                "topic": getattr(q, "question_type", None),
                "category": getattr(q, "category", None),
                "audio_url": getattr(q, "audio_url", None),
                "expected_answer": getattr(q, "expected_answer", None),
                "job_profile_question_id": q.id,
            }
            for q in selected
        ],
    }


def snapshotted_questions(interview) -> list[dict] | None:
    plan = getattr(interview, "question_plan", None)
    if not isinstance(plan, dict) or "questions" not in plan:
        return None
    return copy.deepcopy(plan["questions"])


def validate_initial_questions(interview, questions_data: list[dict]) -> list[dict]:
    total = planned_count(interview)
    if total is None:
        return questions_data
    if len(questions_data) != total:
        raise fastapi.HTTPException(
            503,
            "The complete question set is not ready. Retry preparation; your planned count has not changed.",
        )
    if any(not isinstance(q.get("text"), str) or not q["text"].strip() for q in questions_data):
        raise fastapi.HTTPException(503, "Question preparation returned an invalid prompt. Please retry.")
    normalized = [
        re.sub(r"\s+", " ", str(q.get("text", ""))).strip().casefold()
        for q in questions_data
    ]
    if any(not q for q in normalized) or len(set(normalized)) != total:
        raise fastapi.HTTPException(
            503,
            "Question preparation returned missing or repeated questions. Please retry.",
        )
    frozen = snapshotted_questions(interview)
    if frozen is not None and [q["text"] for q in frozen] != [
        q.get("text") for q in questions_data
    ]:
        raise fastapi.HTTPException(
            409, "The approved sitting snapshot cannot be replaced."
        )
    # Defense at the persistence boundary, even if a generator forgets to
    # disable its usual dynamic follow-up strategy.
    if frozen is not None:
        return [
            {**source, "audio_url": item.get("audio_url"), "follow_up_strategy": None}
            for source, item in zip(frozen, questions_data)
        ]
    return [{**q, "follow_up_strategy": None} for q in questions_data]
