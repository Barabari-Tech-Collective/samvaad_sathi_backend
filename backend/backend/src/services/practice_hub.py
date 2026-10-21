"""Deterministic, evidence-safe data for the student Practice hub.

Gate 2 intentionally does not call an LLM.  The hub may only recommend a focus
when recent completed Practice interviews contain enough transcript-backed
answers and an existing report provides a matching signal.  Missing evidence
is returned as progress towards the threshold, never converted into a score.
"""

from __future__ import annotations

import collections
import datetime
from dataclasses import dataclass
from typing import Any, Iterable, Literal

import sqlalchemy
import fastapi
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.interview import Interview
from src.models.db.interview_question import InterviewQuestion
from src.models.db.pacing_practice import PacingPracticeSession
from src.models.db.practice_drill_completion import PracticeDrillCompletion
from src.models.db.question_attempt import QuestionAttempt
from src.models.db.structure_practice import StructurePractice, StructurePracticeAnswer
from src.models.db.summary_report import SummaryReport
from src.models.schemas.interview import (
    PracticeHubActivity,
    PracticeHubFocus,
    PracticeHubResponse,
    PracticeHubResumeCard,
)
from src.services.whisper import transcription_is_usable

FOCUS_EVIDENCE_REQUIRED = 3
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class _FocusDefinition:
    module: Literal["structure", "pronunciation", "pace", "warm_up", "think_fast"]
    title: str
    href: str
    keywords: tuple[str, ...]


_FOCUS_DEFINITIONS = (
    _FocusDefinition(
        "structure",
        "Structure your answers",
        "/structure-your-answer",
        ("structure", "star", "prep", "organis", "order", "example", "concise"),
    ),
    _FocusDefinition(
        "pronunciation",
        "Make key words easier to understand",
        "/pronunciation-practice",
        ("pronunciation", "pronounce", "intelligib", "mispronoun"),
    ),
    _FocusDefinition(
        "pace",
        "Control your pace",
        "/control-your-pace",
        ("pace", "filler", "pause", "too fast", "too slow", "wpm", "fluency"),
    ),
    _FocusDefinition(
        "warm_up",
        "Practise your first two minutes",
        "/warm-up-chat",
        ("introduc", "tell me about yourself", "motivation", "confidence", "opening"),
    ),
    _FocusDefinition(
        "think_fast",
        "Think clearly under pressure",
        "/think-fast",
        ("improvis", "think fast", "spontaneous", "unexpected", "under pressure"),
    ),
)


def _transcript_text(transcription: Any) -> str:
    if isinstance(transcription, str):
        return transcription.strip()
    if not isinstance(transcription, dict):
        return ""
    for key in ("text", "transcription", "transcript"):
        value = transcription.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def has_evidence(attempt: QuestionAttempt) -> bool:
    """An answer is focus evidence only after it is durably saved and transcribed."""

    return bool(
        attempt.answer_saved_at
        and _transcript_text(attempt.transcription)
        and transcription_is_usable(attempt.transcription)
        and getattr(attempt, "analysis_json", None)
    )


def _report_action_items(report_json: Any) -> list[str]:
    if not isinstance(report_json, dict):
        return []
    items: list[str] = []
    overall = report_json.get("overallFeedback")
    if isinstance(overall, dict):
        for section in overall.values():
            if not isinstance(section, dict):
                continue
            steps = section.get("actionableSteps")
            if not isinstance(steps, list):
                continue
            for step in steps:
                if isinstance(step, str):
                    items.append(step)
                elif isinstance(step, dict):
                    for key in ("title", "description"):
                        value = step.get(key)
                        if isinstance(value, str):
                            items.append(value)
    insights = report_json.get("actionableInsights")
    if isinstance(insights, dict):
        groups = insights.get("groups")
        if isinstance(groups, list):
            for group in groups:
                if not isinstance(group, dict):
                    continue
                values = group.get("items")
                if isinstance(values, list):
                    items.extend(
                        str(value) for value in values if isinstance(value, str)
                    )
    return items


def _report_percentages(report_json: Any) -> tuple[float | None, float | None]:
    if not isinstance(report_json, dict):
        return None, None
    score_summary = report_json.get("scoreSummary")
    if isinstance(score_summary, dict):
        knowledge = score_summary.get("knowledgeCompetence")
        speech = score_summary.get("speechAndStructure")
        return (
            (
                float(knowledge.get("percentage"))
                if isinstance(knowledge, dict)
                and isinstance(knowledge.get("percentage"), (int, float))
                else None
            ),
            (
                float(speech.get("percentage"))
                if isinstance(speech, dict)
                and isinstance(speech.get("percentage"), (int, float))
                else None
            ),
        )
    metrics = report_json.get("metrics")
    if isinstance(metrics, dict):
        knowledge = metrics.get("knowledgeCompetence")
        speech = metrics.get("speechStructure")
        return (
            (
                float(knowledge.get("averagePct"))
                if isinstance(knowledge, dict)
                and isinstance(knowledge.get("averagePct"), (int, float))
                else None
            ),
            (
                float(speech.get("averagePct"))
                if isinstance(speech, dict)
                and isinstance(speech.get("averagePct"), (int, float))
                else None
            ),
        )
    return None, None


def infer_focus(
    report_payloads: Iterable[Any], evidence_count: int
) -> PracticeHubFocus | None:
    """Pick one supported focus using fixed rules, never generated claims."""

    if evidence_count < FOCUS_EVIDENCE_REQUIRED:
        return None

    keyword_counts: collections.Counter[str] = collections.Counter()
    knowledge_scores: list[float] = []
    speech_scores: list[float] = []
    for payload in report_payloads:
        combined = " ".join(_report_action_items(payload)).lower()
        for definition in _FOCUS_DEFINITIONS:
            if any(keyword in combined for keyword in definition.keywords):
                keyword_counts[definition.module] += 1
        knowledge, speech = _report_percentages(payload)
        if knowledge is not None:
            knowledge_scores.append(knowledge)
        if speech is not None:
            speech_scores.append(speech)

    chosen: _FocusDefinition | None = None
    if keyword_counts:
        # Definition order is the deterministic tie-breaker.
        chosen = max(
            _FOCUS_DEFINITIONS,
            key=lambda definition: keyword_counts.get(definition.module, 0),
        )
        if keyword_counts.get(chosen.module, 0) == 0:
            chosen = None
    elif speech_scores and sum(speech_scores) / len(speech_scores) < 65:
        chosen = next(item for item in _FOCUS_DEFINITIONS if item.module == "pace")
    elif knowledge_scores and sum(knowledge_scores) / len(knowledge_scores) < 65:
        chosen = next(item for item in _FOCUS_DEFINITIONS if item.module == "structure")

    if chosen is None:
        return None
    return PracticeHubFocus(
        module=chosen.module,
        title=chosen.title,
        href=chosen.href,
        reason=f"Based on {evidence_count} answered questions in your recent interviews.",
        evidence_answer_count=evidence_count,
    )


def _week_start(now: datetime.datetime) -> datetime.datetime:
    local_now = now.astimezone(_IST)
    local_start = datetime.datetime.combine(
        local_now.date() - datetime.timedelta(days=local_now.weekday()),
        datetime.time.min,
        tzinfo=_IST,
    )
    return local_start.astimezone(datetime.timezone.utc)


def _streak(activity_times: Iterable[datetime.datetime], now: datetime.datetime) -> int:
    days = {
        value.astimezone(_IST).date() for value in activity_times if value is not None
    }
    if not days:
        return 0
    today = now.astimezone(_IST).date()
    cursor = today if today in days else today - datetime.timedelta(days=1)
    count = 0
    while cursor in days:
        count += 1
        cursor -= datetime.timedelta(days=1)
    return count


async def build_practice_hub(
    session: AsyncSession,
    *,
    user_id: int,
    now: datetime.datetime | None = None,
) -> PracticeHubResponse:
    current_time = now or datetime.datetime.now(datetime.timezone.utc)
    week_start = _week_start(current_time)

    resume_stmt = (
        sqlalchemy.select(Interview)
        .where(
            Interview.user_id == user_id,
            Interview.interview_mode == "practice",
            Interview.status.in_(("active", "paused", "preparing")),
            sqlalchemy.or_(
                Interview.expires_at.is_(None), Interview.expires_at > current_time
            ),
        )
        .order_by(Interview.created_at.desc(), Interview.id.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
    resumable = (await session.execute(resume_stmt)).scalar_one_or_none()
    resume_card: PracticeHubResumeCard | None = None
    if resumable is not None:
        from src.services.interview_course_access import require_interview_course_access
        from src.services.interview_question_plan import planned_count

        # One statement gives a coherent count of persisted questions and their
        # latest attempts. Detached or foreign-question attempts never join.
        latest = (
            sqlalchemy.select(
                QuestionAttempt.question_id,
                sqlalchemy.func.max(QuestionAttempt.id).label("attempt_id"),
            )
            .where(
                QuestionAttempt.interview_id == resumable.id,
                QuestionAttempt.question_id.is_not(None),
            )
            .group_by(QuestionAttempt.question_id)
            .subquery()
        )
        durable = sqlalchemy.or_(
            QuestionAttempt.answer_saved_at.is_not(None),
            sqlalchemy.and_(
                QuestionAttempt.audio_url.is_not(None), QuestionAttempt.audio_url != ""
            ),
            sqlalchemy.and_(
                QuestionAttempt.transcription.is_not(None),
                QuestionAttempt.transcription != sqlalchemy.JSON.NULL,
            ),
        )
        counts = (
            await session.execute(
                sqlalchemy.select(
                    sqlalchemy.func.count(InterviewQuestion.id),
                    sqlalchemy.func.count(QuestionAttempt.id).filter(durable),
                )
                .select_from(InterviewQuestion)
                .outerjoin(latest, latest.c.question_id == InterviewQuestion.id)
                .outerjoin(QuestionAttempt, QuestionAttempt.id == latest.c.attempt_id)
                .where(InterviewQuestion.interview_id == resumable.id)
            )
        ).one()
        persisted_count, saved_count = map(int, counts)
        total_questions = persisted_count
        answered_questions = saved_count
        readiness = "ready"
        try:
            if resumable.question_plan is not None and not isinstance(
                resumable.question_plan, dict
            ):
                raise ValueError("Malformed plan")
            declared_count = planned_count(resumable)
            if declared_count is not None:
                total_questions = declared_count
                if persisted_count > declared_count:
                    raise ValueError("Question snapshot exceeds the fixed plan")
                if persisted_count < declared_count:
                    readiness = "preparing"
            if persisted_count == 0:
                retained = (
                    await session.execute(
                        sqlalchemy.select(QuestionAttempt.id)
                        .where(
                            QuestionAttempt.interview_id == resumable.id,
                            durable,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if retained is not None:
                    raise ValueError(
                        "Retained evidence has no recoverable question snapshot"
                    )
            if persisted_count == 0 or resumable.status == "preparing":
                readiness = "preparing"
        except ValueError:
            # No invented N or zero progress for a malformed/overfilled plan.
            total_questions = answered_questions = None
            readiness = "plan_unavailable"
        try:
            await require_interview_course_access(session, resumable)
        except fastapi.HTTPException as error:
            code = error.detail.get("code") if isinstance(error.detail, dict) else None
            if error.status_code == 403 and code in {
                "COURSE_ACCESS_REVOKED",
                "COURSE_ACCESS_UNVERIFIED",
            }:
                readiness = (
                    "enrollment_revoked"
                    if code == "COURSE_ACCESS_REVOKED"
                    else "enrollment_unverified"
                )
            else:
                raise
        resume_card = PracticeHubResumeCard(
            interview_id=resumable.id,
            track=resumable.track,
            difficulty=resumable.difficulty,
            status=resumable.status,
            answered_questions=answered_questions,
            total_questions=total_questions,
            expires_at=resumable.expires_at,
            readiness=readiness,
        )

    recent_stmt = (
        sqlalchemy.select(Interview)
        .where(
            Interview.user_id == user_id,
            Interview.interview_mode == "practice",
            Interview.status == "completed",
        )
        .order_by(Interview.completed_at.desc(), Interview.id.desc())
        .limit(3)
    )
    recent = list((await session.execute(recent_stmt)).scalars().all())
    recent_ids = [item.id for item in recent]
    evidence_count = 0
    report_payloads: list[Any] = []
    if recent_ids:
        attempts_stmt = sqlalchemy.select(QuestionAttempt).where(
            QuestionAttempt.interview_id.in_(recent_ids),
            QuestionAttempt.answer_saved_at.is_not(None),
        )
        attempts = (await session.execute(attempts_stmt)).scalars().all()
        evidence_count = sum(1 for attempt in attempts if has_evidence(attempt))
        reports_stmt = sqlalchemy.select(SummaryReport).where(
            SummaryReport.interview_id.in_(recent_ids)
        )
        reports = (await session.execute(reports_stmt)).scalars().all()
        report_payloads = [report.report_json for report in reports]

    mocks_stmt = sqlalchemy.select(Interview.completed_at).where(
        Interview.user_id == user_id,
        Interview.interview_mode == "practice",
        Interview.status == "completed",
        Interview.completed_at >= week_start,
    )
    mock_times = [
        value for value in (await session.execute(mocks_stmt)).scalars().all() if value
    ]

    pacing_stmt = sqlalchemy.select(PacingPracticeSession.updated_at).where(
        PacingPracticeSession.user_id == user_id,
        PacingPracticeSession.status == "completed",
        PacingPracticeSession.updated_at >= week_start,
    )
    pacing_times = [
        value for value in (await session.execute(pacing_stmt)).scalars().all() if value
    ]

    structure_stmt = (
        sqlalchemy.select(sqlalchemy.func.max(StructurePracticeAnswer.analyzed_at))
        .join(
            StructurePractice,
            StructurePractice.id == StructurePracticeAnswer.practice_id,
        )
        .where(
            StructurePractice.user_id == user_id,
            StructurePracticeAnswer.analyzed_at.is_not(None),
            StructurePracticeAnswer.analyzed_at >= week_start,
        )
        .group_by(StructurePractice.id)
    )
    structure_times = [
        value
        for value in (await session.execute(structure_stmt)).scalars().all()
        if value
    ]

    completion_stmt = sqlalchemy.select(PracticeDrillCompletion.completed_at).where(
        PracticeDrillCompletion.user_id == user_id,
        PracticeDrillCompletion.completed_at >= week_start,
    )
    completion_times = [
        value
        for value in (await session.execute(completion_stmt)).scalars().all()
        if value
    ]
    drill_times = pacing_times + structure_times + completion_times

    return PracticeHubResponse(
        activity=PracticeHubActivity(
            practice_days_streak=_streak(mock_times + drill_times, current_time),
            completed_mocks_this_week=len(mock_times),
            completed_drills_this_week=len(drill_times),
        ),
        resume=resume_card,
        focus=infer_focus(report_payloads, evidence_count),
        focus_evidence_answer_count=evidence_count,
        focus_evidence_required=FOCUS_EVIDENCE_REQUIRED,
    )
