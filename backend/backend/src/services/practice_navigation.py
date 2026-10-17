"""Server-owned Practice interview navigation and snapshot integrity.

The browser may cache questions for resilience, but it is never authoritative
about which question comes next.  These helpers keep the state transition pure
so the HTTP route can hold one database row lock, apply the transition, and
commit exactly once.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

AdvanceAction = Literal["answer_saved", "skip", "continue_without_follow_up"]


class NavigationConflict(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class NavigationResult:
    current_question_id: int | None
    state_version: int
    complete_ready: bool
    duplicate: bool = False


def question_snapshot_hash(questions: Iterable[object]) -> str:
    """Hash immutable base-question content in its persisted order.

    Adaptive follow-ups are intentionally excluded: they are immutable once
    created, but are appended in response to a student's answer and therefore
    cannot be part of the question set frozen at interview start.
    """

    payload = [
        {
            "id": int(question.id),
            "order": int(question.order),
            "text": str(question.text),
            "topic": getattr(question, "topic", None),
            "category": getattr(question, "category", None),
            "expected_answer": getattr(question, "expected_answer", None),
            "follow_up_strategy": getattr(question, "follow_up_strategy", None),
        }
        for question in questions
        if not getattr(question, "is_follow_up", False)
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def has_durable_answer(attempt: object | None) -> bool:
    return bool(
        attempt
        and (
            getattr(attempt, "answer_saved_at", None) is not None
            or getattr(attempt, "audio_url", None)
            or getattr(attempt, "transcription", None) is not None
        )
    )


def initialize_navigation(
    interview: object,
    questions: Sequence[object],
    attempts_by_question_id: dict[int, object],
) -> bool:
    """Backfill a checkpoint for interviews created before Gate 2.

    New interviews are initialized atomically when their question batch is
    persisted.  This compatibility path preserves existing resumable sessions
    without regenerating or reordering anything.
    """

    changed = False
    base_questions = [q for q in questions if not getattr(q, "is_follow_up", False)]
    if base_questions and int(getattr(interview, "question_set_version", 0) or 0) == 0:
        interview.question_set_version = 1
        interview.question_set_hash = question_snapshot_hash(base_questions)
        interview.question_set_frozen_at = datetime.datetime.now(datetime.timezone.utc)
        changed = True

    if questions and getattr(interview, "current_question_id", None) is None:
        # A legacy interview had no explicit cursor. Preserve the prior resume
        # behaviour once, then every subsequent transition is authoritative.
        candidate = next(
            (
                q
                for q in questions
                if getattr(q, "status", None) != "skipped"
                and not has_durable_answer(attempts_by_question_id.get(int(q.id)))
            ),
            None,
        )
        if candidate is not None:
            interview.current_question_id = int(candidate.id)
            interview.state_version = max(
                1, int(getattr(interview, "state_version", 0) or 0)
            )
            changed = True
    return changed


def apply_navigation_transition(
    *,
    interview: object,
    questions: Sequence[object],
    attempts_by_question_id: dict[int, object],
    from_question_id: int,
    expected_state_version: int,
    transition_id: str,
    action: AdvanceAction,
    answer_version: int | None,
    now: datetime.datetime | None = None,
) -> NavigationResult:
    """Validate and apply one question transition to already-locked rows."""

    previous = getattr(interview, "last_transition_response", None)
    if getattr(interview, "last_transition_id", None) == transition_id and isinstance(
        previous, dict
    ):
        return NavigationResult(
            current_question_id=previous.get("currentQuestionId"),
            state_version=int(previous["stateVersion"]),
            complete_ready=bool(previous["completeReady"]),
            duplicate=True,
        )

    actual_version = int(getattr(interview, "state_version", 0) or 0)
    if expected_state_version != actual_version:
        raise NavigationConflict(
            "INTERVIEW_STATE_CONFLICT",
            "This interview moved forward in another tab. Reload the saved checkpoint.",
        )

    current_question_id = getattr(interview, "current_question_id", None)
    if current_question_id != from_question_id:
        raise NavigationConflict(
            "QUESTION_CHECKPOINT_CHANGED",
            "This is no longer the current question. Reload the saved checkpoint.",
        )

    index_by_id = {int(question.id): index for index, question in enumerate(questions)}
    if from_question_id not in index_by_id:
        raise NavigationConflict(
            "QUESTION_NOT_IN_SNAPSHOT",
            "This question is not part of the saved interview.",
        )

    question = questions[index_by_id[from_question_id]]
    attempt = attempts_by_question_id.get(from_question_id)
    saved = has_durable_answer(attempt)
    if action != "skip" and not saved:
        raise NavigationConflict(
            "ANSWER_NOT_SAVED",
            "Save this answer before continuing. Your current draft remains on this device.",
        )
    if saved and answer_version is not None:
        actual_answer_version = int(getattr(attempt, "answer_version", 0) or 0)
        if answer_version != actual_answer_version:
            raise NavigationConflict(
                "ANSWER_VERSION_CONFLICT",
                "A newer answer is already saved for this question. Reload before continuing.",
            )

    is_adaptive_parent = bool(
        getattr(question, "follow_up_strategy", None)
        and not getattr(question, "is_follow_up", False)
    )
    follow_up_pending = bool(
        is_adaptive_parent
        and attempt is not None
        and getattr(attempt, "follow_up_disposition", None) == "pending"
    )
    if follow_up_pending and action == "answer_saved":
        raise NavigationConflict(
            "FOLLOW_UP_PENDING",
            "An adaptive follow-up is still being prepared. Wait or continue without it.",
        )

    if action == "continue_without_follow_up":
        if not is_adaptive_parent:
            raise NavigationConflict(
                "FOLLOW_UP_NOT_AVAILABLE",
                "This question has no adaptive follow-up to skip.",
            )

    # Validate the complete transition before mutating any ORM object. A
    # rejected request may share its session with exception handlers and must
    # never leave dirty in-memory state that a later commit could persist.
    timestamp = now or datetime.datetime.now(datetime.timezone.utc)
    if action == "skip":
        if attempt is not None:
            attempt.attempt_state = "skipped"
            attempt.skipped_at = timestamp
            if is_adaptive_parent:
                attempt.follow_up_disposition = "skipped"
                attempt.follow_up_resolved_at = timestamp
        question.status = "skipped"
    else:
        if attempt is not None:
            attempt.attempt_state = "answer_saved"
        question.status = "answered"

    if action == "continue_without_follow_up":
        if attempt is not None:
            attempt.follow_up_disposition = "skipped"
            attempt.follow_up_resolved_at = timestamp
        for child in questions:
            if (
                getattr(child, "is_follow_up", False)
                and getattr(child, "parent_question_id", None) == from_question_id
            ):
                child.status = "skipped"
                child_attempt = attempts_by_question_id.get(int(child.id))
                if child_attempt is not None:
                    child_attempt.attempt_state = "skipped"
                    child_attempt.skipped_at = timestamp

    next_question_id: int | None = None
    for candidate in questions[index_by_id[from_question_id] + 1 :]:
        if getattr(candidate, "status", None) == "skipped":
            continue
        next_question_id = int(candidate.id)
        break

    next_version = actual_version + 1
    interview.current_question_id = next_question_id
    interview.state_version = next_version
    interview.last_transition_id = transition_id
    response = {
        "currentQuestionId": next_question_id,
        "stateVersion": next_version,
        "completeReady": next_question_id is None,
    }
    interview.last_transition_response = response
    return NavigationResult(
        current_question_id=next_question_id,
        state_version=next_version,
        complete_ready=next_question_id is None,
    )


__all__ = [
    "AdvanceAction",
    "NavigationConflict",
    "NavigationResult",
    "apply_navigation_transition",
    "has_durable_answer",
    "initialize_navigation",
    "question_snapshot_hash",
]
