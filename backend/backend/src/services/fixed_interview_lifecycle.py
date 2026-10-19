import datetime

import fastapi

from src.services.interview_question_plan import planned_count
from src.services.practice_navigation import has_durable_answer


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def begin(interview, questions, *, now=None):
    now = now or utcnow()
    total = planned_count(interview)
    if total is None:
        return {
            "plannedQuestionCount": None,
            "answerDeadlineAt": None,
            "serverTime": now.isoformat(),
        }
    if interview.status != "active" or len(questions) != total:
        raise fastapi.HTTPException(
            409, "Prepare the complete question set before beginning."
        )
    if getattr(interview, "started_at", None) is None:
        interview.started_at = now
        if interview.interview_mode == "assessment":
            interview.answer_deadline_at = now + datetime.timedelta(
                seconds=interview.question_plan["duration_seconds"]
            )
    return {
        "plannedQuestionCount": total,
        "answerDeadlineAt": (
            interview.answer_deadline_at.isoformat()
            if getattr(interview, "answer_deadline_at", None)
            else None
        ),
        "serverTime": now.isoformat(),
    }


def require_answer_window(interview, *, attempt=None, now=None):
    if planned_count(interview) is None:
        return
    if getattr(interview, "started_at", None) is None:
        raise fastapi.HTTPException(
            409, "Begin the prepared interview before answering."
        )
    deadline = getattr(interview, "answer_deadline_at", None)
    now = now or utcnow()
    if deadline is None or now <= deadline:
        return
    created = getattr(attempt, "created_at", None) if attempt is not None else None
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=datetime.timezone.utc)
    if (
        created is not None
        and created <= deadline
        and now <= deadline + datetime.timedelta(seconds=60)
    ):
        return
    raise fastapi.HTTPException(
        409,
        "The answer window has ended. Submit the saved interview to get your receipt.",
    )


def receipt(interview, questions, attempts, *, now=None):
    now = now or utcnow()
    total = planned_count(interview)
    if total is None:
        return None
    ids = {q.id for q in questions}
    latest = {
        a.question_id: a
        for a in sorted(attempts, key=lambda a: a.id)
        if a.question_id in ids
    }
    answered = {qid for qid, attempt in latest.items() if has_durable_answer(attempt)}
    skipped = {
        q.id for q in questions if q.status == "skipped" and q.id not in answered
    }
    presented = set(latest) | skipped
    deadline = getattr(interview, "answer_deadline_at", None)
    incomplete = len(answered | skipped) != total
    if incomplete and (deadline is None or now <= deadline):
        raise fastapi.HTTPException(
            409, "Answer or explicitly skip the remaining questions before submitting."
        )
    return {
        "version": 1,
        "interviewId": interview.id,
        "planned": total,
        "presented": len(presented),
        "answered": len(answered),
        "skipped": len(skipped),
        "unresolved": len(presented - answered - skipped),
        "unreached": total - len(presented),
        "reason": "time_limit" if incomplete else "completed",
        "submittedAt": now.isoformat(),
    }
