"""Deterministic evidence and coaching rules for Practice reports.

The LLM may phrase feedback, but it never decides whether evidence is sufficient,
whether a score can be published, or which product route exists.
"""

from __future__ import annotations

import copy
import math
import uuid
from collections.abc import Iterable
from typing import Any

from src.services.whisper import assess_transcription_evidence

MINIMUM_REPORT_EVIDENCE = 3
REPORT_VERSION = "practice-report-v1"

_DRILLS = (
    {
        "module": "structure",
        "title": "Structure your answers",
        "description": "Use a simple framework to make your next answer easier to follow.",
        "href": "/structure-your-answer",
        "keywords": (
            "structure",
            "star",
            "prep",
            "order",
            "example",
            "concise",
            "depth",
        ),
    },
    {
        "module": "pronunciation",
        "title": "Make key words easier to understand",
        "description": "Practise important role words slowly, then use them in a complete answer.",
        "href": "/pronunciation-practice",
        "keywords": ("pronunciation", "pronounce", "intelligib", "mispronoun"),
    },
    {
        "module": "pace",
        "title": "Control your pace",
        "description": "Use calm pauses and reduce filler words while keeping your answer moving.",
        "href": "/control-your-pace",
        "keywords": (
            "pace",
            "pacing",
            "filler",
            "pause",
            "too fast",
            "too slow",
            "fluency",
        ),
    },
    {
        "module": "warm_up",
        "title": "Practise your first two minutes",
        "description": "Build a clear introduction and explain why this role interests you.",
        "href": "/warm-up-chat",
        "keywords": (
            "introduction",
            "introduce",
            "tell me about yourself",
            "opening",
            "motivation",
        ),
    },
    {
        "module": "think_fast",
        "title": "Think clearly under pressure",
        "description": "Practise giving a clear beginning, supporting point and ending without preparation.",
        "href": "/think-fast",
        "keywords": (
            "improvis",
            "think fast",
            "spontaneous",
            "unexpected",
            "under pressure",
        ),
    },
)

_EVIDENCE_MESSAGES = {
    "missing_transcript": "No transcript was available, so this answer was not scored.",
    "invalid_duration": "The recording duration was invalid, so this answer was not scored.",
    "no_speech_detected": "No clear speech was detected, so this answer was not scored.",
    "insufficient_speech": "There was not enough speech for reliable feedback.",
    "low_confidence": "The transcript was too uncertain for reliable feedback.",
}


def _transcript(attempt: Any) -> dict[str, Any] | None:
    value = getattr(attempt, "transcription", None)
    return value if isinstance(value, dict) else None


def _text(attempt: Any) -> str:
    transcription = _transcript(attempt) or {}
    return str(
        transcription.get("text") or transcription.get("transcript") or ""
    ).strip()


def _is_saved(attempt: Any) -> bool:
    return bool(
        getattr(attempt, "answer_saved_at", None)
        or getattr(attempt, "audio_url", None)
        or getattr(attempt, "transcription", None)
    )


def _has_analysis(attempt: Any) -> bool:
    return bool(getattr(attempt, "analysis_json", None))


def question_evidence(attempt: Any | None) -> dict[str, Any]:
    if attempt is None or not _is_saved(attempt):
        return {
            "status": "not_attempted",
            "observation": None,
            "interpretation": None,
            "uncertainty": "No saved answer was available for this question.",
        }

    transcription = _transcript(attempt)
    evidence = (
        transcription.get("evidence")
        if transcription and isinstance(transcription.get("evidence"), dict)
        else assess_transcription_evidence(transcription)
    )
    word_count = int(evidence.get("word_count") or len(_text(attempt).split()))
    observation = (
        f"A {word_count}-word spoken answer was captured."
        if word_count
        else "A recording was saved, but no reliable transcript was captured."
    )
    if evidence.get("status") != "usable":
        reason = str(evidence.get("reason") or "missing_transcript")
        return {
            "status": (
                "insufficient"
                if reason in {"insufficient_speech", "no_speech_detected"}
                else "unavailable"
            ),
            "observation": observation,
            "interpretation": None,
            "uncertainty": _EVIDENCE_MESSAGES.get(
                reason, "This answer could not support reliable feedback."
            ),
        }
    if not _has_analysis(attempt):
        return {
            "status": "needs_review",
            "observation": observation,
            "interpretation": None,
            "uncertainty": "Your answer is saved, but its coaching analysis is not ready yet.",
        }
    return {
        "status": "evaluated",
        "observation": observation,
        "interpretation": None,
        "uncertainty": None,
    }


def build_evidence_summary(
    attempts: Iterable[Any], total_questions: int
) -> dict[str, Any]:
    attempts = list(attempts)
    evidence_items = [question_evidence(attempt) for attempt in attempts]
    saved = sum(1 for attempt in attempts if _is_saved(attempt))
    usable = sum(
        1 for item in evidence_items if item["status"] in {"evaluated", "needs_review"}
    )
    evaluated = sum(1 for item in evidence_items if item["status"] == "evaluated")
    coverage = round((evaluated / total_questions) * 100) if total_questions else 0
    high_threshold = max(MINIMUM_REPORT_EVIDENCE, math.ceil(total_questions * 0.8))
    if evaluated >= high_threshold:
        confidence = "high"
        message = f"Feedback is based on {evaluated} evaluated answers."
    elif evaluated >= MINIMUM_REPORT_EVIDENCE:
        confidence = "medium"
        message = f"Feedback is based on {evaluated} evaluated answers; unanswered items were excluded."
    elif usable >= MINIMUM_REPORT_EVIDENCE:
        confidence = "low"
        message = "Enough answers were captured, but some coaching analysis is still unavailable."
    else:
        confidence = "insufficient"
        message = (
            f"Complete at least {MINIMUM_REPORT_EVIDENCE} clear spoken answers to unlock scores "
            "and a personalised drill."
        )
    return {
        "totalQuestions": total_questions,
        "savedAnswers": saved,
        "usableAnswers": usable,
        "evaluatedAnswers": evaluated,
        "minimumRequired": MINIMUM_REPORT_EVIDENCE,
        "coveragePercentage": max(0, min(100, coverage)),
        "confidence": confidence,
        "statusMessage": message,
    }


def _feedback_text(report: dict[str, Any]) -> str:
    values: list[str] = []
    for item in report.get("questionAnalysis") or []:
        if not isinstance(item, dict):
            continue
        feedback = item.get("feedback")
        if isinstance(feedback, dict):
            values.extend(
                str(value) for value in feedback.values() if isinstance(value, str)
            )
    speech = report.get("speechFluencyFeedback")
    if isinstance(speech, dict):
        values.extend(str(value) for value in speech.values() if isinstance(value, str))
    return " ".join(values).lower()


def _fixed_recommendation(
    report: dict[str, Any], evidence_count: int
) -> dict[str, Any]:
    combined = _feedback_text(report)
    keyword_counts = {
        drill["module"]: sum(combined.count(word) for word in drill["keywords"])
        for drill in _DRILLS
    }
    chosen = max(
        _DRILLS,
        key=lambda drill: keyword_counts[drill["module"]],
    )
    if keyword_counts[chosen["module"]] == 0:
        chosen = None
    if chosen is None:
        scores = report.get("scoreSummary") or {}
        knowledge = (scores.get("knowledgeCompetence") or {}).get("percentage")
        speech = (scores.get("speechAndStructure") or {}).get("percentage")
        chosen_module = (
            "structure"
            if isinstance(knowledge, (int, float))
            and (not isinstance(speech, (int, float)) or knowledge <= speech)
            else "pace"
        )
        chosen = next(drill for drill in _DRILLS if drill["module"] == chosen_module)
    return {key: value for key, value in chosen.items() if key != "keywords"} | {
        "reason": f"This is the clearest next step supported by {evidence_count} evaluated answers.",
        "evidenceAnswerCount": evidence_count,
    }


def apply_report_contract(
    raw_report: dict[str, Any],
    *,
    interview_id: int,
    attempts_by_question_id: dict[int, Any],
    ordered_questions: list[Any],
    generated_at: str,
    llm_succeeded: bool,
) -> dict[str, Any]:
    """Apply code-owned publication gates to an LLM or deterministic report."""

    report = copy.deepcopy(raw_report)
    evidence_summary = build_evidence_summary(
        attempts_by_question_id.values(), len(ordered_questions)
    )
    evaluated = evidence_summary["evaluatedAnswers"]
    report["reportId"] = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"samvaad:practice-report:{interview_id}:{REPORT_VERSION}",
        )
    )
    report["reportVersion"] = REPORT_VERSION
    report["generatedAt"] = generated_at
    report["evidenceSummary"] = evidence_summary

    question_items = report.get("questionAnalysis") or []
    for index, question in enumerate(ordered_questions):
        if index >= len(question_items) or not isinstance(question_items[index], dict):
            continue
        item = question_items[index]
        evidence = question_evidence(attempts_by_question_id.get(question.id))
        feedback = item.get("feedback")
        if evidence["status"] == "evaluated" and isinstance(feedback, dict):
            interpretation = feedback.get("areasOfImprovement") or feedback.get(
                "strengths"
            )
            evidence["interpretation"] = str(interpretation) if interpretation else None
        elif evidence["status"] != "evaluated":
            item["feedback"] = None
        item["evidence"] = evidence

    if evaluated < MINIMUM_REPORT_EVIDENCE:
        report["reportStatus"] = (
            "needs_review"
            if evidence_summary["usableAnswers"] >= MINIMUM_REPORT_EVIDENCE
            else "insufficient_evidence"
        )
        report["scoreSummary"] = None
        report["recommendedPractice"] = None
        report["speechFluencyFeedback"] = None
        report["nextSteps"] = None
        report["finalTip"] = {
            "title": "Your saved work still counts",
            "description": evidence_summary["statusMessage"],
        }
        report["uncertaintyNotice"] = (
            "We have not shown an overall score because too few answers currently have reliable evidence."
        )
        return report

    report["reportStatus"] = "ready" if llm_succeeded else "partial"
    report["recommendedPractice"] = _fixed_recommendation(report, evaluated)
    report["nextSteps"] = [{"title": report["recommendedPractice"]["title"]}]
    report["uncertaintyNotice"] = (
        "Scores describe only the evaluated answers in this interview. They are coaching signals, "
        "not hiring decisions."
    )
    if not llm_succeeded:
        report["speechFluencyFeedback"] = None
        report["finalTip"] = {
            "title": "Some coaching details are delayed",
            "description": "Your evidence-based scores are available. Detailed AI wording can be prepared again later.",
        }
    return report
