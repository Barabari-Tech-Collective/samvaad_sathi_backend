from __future__ import annotations

import datetime
import math
from collections import defaultdict
from typing import Any

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession

from src.models.db.interview import Interview
from src.models.db.interview_question import InterviewQuestion
from src.models.db.pacing_practice import PacingPracticeSession
from src.models.db.pronunciation_practice import PronunciationPractice
from src.models.db.question_attempt import QuestionAttempt
from src.models.db.report import Report
from src.models.db.structure_practice import StructurePractice, StructurePracticeAnswer
from src.models.db.summary_report import SummaryReport
from src.models.db.user import User
from src.models.db.analytics_event import AnalyticsEvent


class AnalyticsService:
    def __init__(self, db: SQLAlchemyAsyncSession) -> None:
        self._db = db

    async def get_student_level_analytics(
        self,
        *,
        user_id: int,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
    ) -> dict[str, Any]:
        interviews = await self._list_interviews(user_id=user_id, start_date=start_date, end_date=end_date)
        if not interviews:
            return {
                "performance": {},
                "skill_breakdown": {},
                "consistency": {},
                "weak_area_tags": [],
                "practice_compliance": {},
                "attempt_behavior": {},
                "follow_up_analytics": {},
            }

        interview_ids = [i.id for i in interviews]
        reports = await self._reports_by_interview(interview_ids)
        summary_reports = await self._summary_reports_by_interview(interview_ids)
        attempts = await self._attempts_for_interviews(interview_ids)
        questions = await self._questions_for_interviews(interview_ids)

        score_points: list[dict[str, Any]] = []
        speech_points: list[float] = []
        knowledge_points: list[float] = []
        metric_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        examples_history: list[dict[str, Any]] = []
        follow_up_scores: list[float] = []
        non_follow_up_scores: list[float] = []

        question_map = {q.id: q for q in questions}

        for interview in interviews:
            report = reports.get(interview.id)
            summary_report = summary_reports.get(interview.id)
            overall = _extract_overall_score(report, summary_report)
            speech = _extract_speech_score(report, summary_report)
            knowledge = _extract_knowledge_score(report, summary_report)
            if overall is not None:
                score_points.append(
                    {
                        "interview_id": interview.id,
                        "created_at": interview.created_at,
                        "overall_score": round(overall, 2),
                        "speech_score": round(speech, 2) if speech is not None else None,
                        "knowledge_score": round(knowledge, 2) if knowledge is not None else None,
                    }
                )
            if speech is not None:
                speech_points.append(speech)
            if knowledge is not None:
                knowledge_points.append(knowledge)

        for qa in attempts:
            interview = _find_interview(interviews, qa.interview_id)
            if interview is None:
                continue
            analysis = qa.analysis_json or {}
            pace = analysis.get("pace") or {}
            pause = analysis.get("pause") or {}
            communication = analysis.get("communication") or {}
            domain = analysis.get("domain") or {}

            wpm = _to_float(pace.get("wpm"))
            pause_score = _normalize_score(_to_float(pause.get("pause_score") or pause.get("score")))
            filler_density = _to_float(
                communication.get("filler_density")
                or communication.get("filler_rate")
                or communication.get("filler_percentage")
            )
            energy = _normalize_score(_to_float(communication.get("energy") or communication.get("energy_score")))
            consistency = _normalize_score(_to_float(communication.get("consistency") or communication.get("consistency_score")))
            technical_accuracy = _normalize_score(
                _to_float(
                    ((domain.get("criteria") or {}).get("correctness") or {}).get("score")
                    or domain.get("domain_score")
                )
            )
            structure_quality = _normalize_score(
                _to_float(communication.get("structure_score") or ((communication.get("criteria") or {}).get("structure") or {}).get("score"))
            )
            relevance = _normalize_score(
                _to_float(((domain.get("criteria") or {}).get("relevance") or {}).get("score"))
            )

            has_examples = bool(
                _to_float(((domain.get("criteria") or {}).get("examples") or {}).get("score"))
                and _to_float(((domain.get("criteria") or {}).get("examples") or {}).get("score")) > 0
            )

            if wpm is not None:
                metric_history["wpm"].append(_history_point(interview, qa, round(wpm, 2)))
            if pause_score is not None:
                metric_history["pause_score"].append(_history_point(interview, qa, round(pause_score, 2)))
            if filler_density is not None:
                metric_history["filler_density"].append(_history_point(interview, qa, round(filler_density, 4)))
            if energy is not None:
                metric_history["energy"].append(_history_point(interview, qa, round(energy, 2)))
            if consistency is not None:
                metric_history["consistency"].append(_history_point(interview, qa, round(consistency, 2)))
            if technical_accuracy is not None:
                metric_history["technical_accuracy"].append(_history_point(interview, qa, round(technical_accuracy, 2)))
            if structure_quality is not None:
                metric_history["structure_quality"].append(_history_point(interview, qa, round(structure_quality, 2)))
            if relevance is not None:
                metric_history["relevance"].append(_history_point(interview, qa, round(relevance, 2)))
            examples_history.append(_history_point(interview, qa, has_examples))

            question = question_map.get(qa.question_id) if qa.question_id is not None else None
            current_score = _avg_non_null([technical_accuracy, structure_quality])
            if question and question.is_follow_up and current_score is not None:
                follow_up_scores.append(current_score)
            elif current_score is not None:
                non_follow_up_scores.append(current_score)

        overall_scores = [point["overall_score"] for point in score_points if point.get("overall_score") is not None]
        overall_scores.sort()

        ordered_scores = sorted(score_points, key=lambda x: x.get("created_at") or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc))
        latest_score = ordered_scores[-1]["overall_score"] if ordered_scores else None
        first_score = ordered_scores[0]["overall_score"] if ordered_scores else None
        avg_last_3 = _avg_non_null([x["overall_score"] for x in ordered_scores[-3:]])
        best_score = max(overall_scores) if overall_scores else None
        improvement = (latest_score - first_score) if latest_score is not None and first_score is not None else None

        reattempt_stats = await self._reattempt_stats(user_id=user_id, start_date=start_date, end_date=end_date)
        interview_times = [i.created_at for i in interviews if i.created_at is not None]
        avg_gap_hours = _average_gap_hours(interview_times)

        practice_stats = await self._practice_compliance(user_id=user_id)

        weak_area_tags = _compute_weak_area_tags(metric_history=metric_history)

        follow_up_delta = None
        if follow_up_scores and non_follow_up_scores:
            follow_up_delta = round(_avg_non_null(follow_up_scores) - _avg_non_null(non_follow_up_scores), 2)

        return {
            "performance": {
                "latest_score": round(latest_score, 2) if latest_score is not None else None,
                "average_last_3": round(avg_last_3, 2) if avg_last_3 is not None else None,
                "best_score": round(best_score, 2) if best_score is not None else None,
                "improvement_rate": round(improvement, 2) if improvement is not None else None,
                "improvement_formula": "latest_score - first_score",
                "score_history": [
                    {
                        "interview_id": item["interview_id"],
                        "created_at": item["created_at"],
                        "overall_score": item["overall_score"],
                        "speech_score": item["speech_score"],
                        "knowledge_score": item["knowledge_score"],
                    }
                    for item in ordered_scores
                ],
            },
            "skill_breakdown": {
                "speech": {
                    "wpm": metric_history.get("wpm", []),
                    "pause_score": metric_history.get("pause_score", []),
                    "filler_density": metric_history.get("filler_density", []),
                    "energy": metric_history.get("energy", []),
                    "consistency": metric_history.get("consistency", []),
                },
                "knowledge": {
                    "technical_accuracy": metric_history.get("technical_accuracy", []),
                    "structure_quality": metric_history.get("structure_quality", []),
                    "relevance": metric_history.get("relevance", []),
                    "examples_given": examples_history,
                },
            },
            "consistency": {
                "score_variance": round(_variance([x["overall_score"] for x in ordered_scores if x.get("overall_score") is not None]), 4),
                "status": _consistency_status(_variance([x["overall_score"] for x in ordered_scores if x.get("overall_score") is not None])),
            },
            "weak_area_tags": weak_area_tags,
            "practice_compliance": practice_stats,
            "attempt_behavior": {
                "interviews_attempted": len(interviews),
                "reattempt_frequency": reattempt_stats,
                "average_time_between_attempts_hours": round(avg_gap_hours, 2) if avg_gap_hours is not None else None,
            },
            "follow_up_analytics": {
                "average_follow_up_score": round(_avg_non_null(follow_up_scores), 2) if follow_up_scores else None,
                "average_non_follow_up_score": round(_avg_non_null(non_follow_up_scores), 2) if non_follow_up_scores else None,
                "delta_follow_up_vs_non_follow_up": follow_up_delta,
            },
        }

    async def get_interview_level_analytics(self, *, interview_id: int) -> dict[str, Any] | None:
        interview_stmt = sqlalchemy.select(Interview).where(Interview.id == interview_id)
        interview_res = await self._db.execute(interview_stmt)
        interview = interview_res.scalar_one_or_none()
        if interview is None:
            return None

        questions_stmt = (
            sqlalchemy.select(InterviewQuestion)
            .where(InterviewQuestion.interview_id == interview_id)
            .order_by(InterviewQuestion.order.asc())
        )
        attempts_stmt = (
            sqlalchemy.select(QuestionAttempt)
            .where(QuestionAttempt.interview_id == interview_id)
            .order_by(QuestionAttempt.id.desc())
        )
        report_stmt = sqlalchemy.select(Report).where(Report.interview_id == interview_id)
        summary_stmt = sqlalchemy.select(SummaryReport).where(SummaryReport.interview_id == interview_id)

        questions = list((await self._db.execute(questions_stmt)).scalars().all())
        attempts_all = list((await self._db.execute(attempts_stmt)).scalars().all())
        report = (await self._db.execute(report_stmt)).scalar_one_or_none()
        summary = (await self._db.execute(summary_stmt)).scalar_one_or_none()

        latest_attempt_by_question: dict[int, QuestionAttempt] = {}
        for attempt in attempts_all:
            if attempt.question_id is not None and attempt.question_id not in latest_attempt_by_question:
                latest_attempt_by_question[attempt.question_id] = attempt

        total_questions = len(questions)
        attempted_questions = len([q for q in questions if q.id in latest_attempt_by_question])
        completion_rate = (attempted_questions / total_questions * 100.0) if total_questions > 0 else 0.0

        first_attempt_time = min((a.created_at for a in attempts_all if a.created_at is not None), default=None)
        last_attempt_time = max((a.created_at for a in attempts_all if a.created_at is not None), default=None)
        duration_seconds = None
        if first_attempt_time and last_attempt_time:
            duration_seconds = max(0, int((last_attempt_time - first_attempt_time).total_seconds()))

        question_items: list[dict[str, Any]] = []
        parent_score_map: dict[int, float] = {}
        follow_up_scores: list[float] = []
        parent_scores_for_followups: list[float] = []

        for question in questions:
            qa = latest_attempt_by_question.get(question.id)
            analysis = qa.analysis_json if qa and qa.analysis_json else {}
            raw_domain = analysis.get("domain") if isinstance(analysis, dict) else {}
            raw_communication = analysis.get("communication") if isinstance(analysis, dict) else {}
            domain = raw_domain if isinstance(raw_domain, dict) else {}
            communication = raw_communication if isinstance(raw_communication, dict) else {}
            strengths = _as_str_list(domain.get("strengths")) + _as_str_list(communication.get("strengths"))
            weaknesses = _as_str_list(domain.get("improvements")) + _as_str_list(communication.get("improvements")) + _as_str_list(
                communication.get("recommendations")
            )
            knowledge_score = _normalize_score(
                _to_float(
                    ((domain.get("criteria") or {}).get("correctness") or {}).get("score")
                    or domain.get("domain_score")
                )
            )
            speech_score = _normalize_score(
                _to_float(
                    communication.get("communication_score")
                    or communication.get("overall_score")
                )
            )
            combined_score = _avg_non_null([knowledge_score, speech_score])

            if combined_score is not None:
                parent_score_map[question.id] = combined_score

            if question.is_follow_up and combined_score is not None:
                follow_up_scores.append(combined_score)
                if question.parent_question_id and question.parent_question_id in parent_score_map:
                    parent_scores_for_followups.append(parent_score_map[question.parent_question_id])

            question_items.append(
                {
                    "question_id": question.id,
                    "order": question.order,
                    "question_text": question.text,
                    "question_type": _question_type(question.category),
                    "is_follow_up": question.is_follow_up,
                    "parent_question_id": question.parent_question_id,
                    "knowledge_score": round(knowledge_score, 2) if knowledge_score is not None else None,
                    "speech_score": round(speech_score, 2) if speech_score is not None else None,
                    "answered": qa is not None,
                    "strength_tags": _unique_preserve_order(strengths)[:8],
                    "weakness_tags": _unique_preserve_order(weaknesses)[:8],
                }
            )

        follow_up_delta = None
        if follow_up_scores and parent_scores_for_followups:
            follow_up_delta = round(_avg_non_null(follow_up_scores) - _avg_non_null(parent_scores_for_followups), 2)

        question_dropoff: list[dict[str, Any]] = []
        for idx in range(1, len(question_items)):
            current = question_items[idx]
            previous = question_items[idx - 1]
            if current.get("knowledge_score") is None or previous.get("knowledge_score") is None:
                continue
            delta = float(current["knowledge_score"]) - float(previous["knowledge_score"])
            question_dropoff.append(
                {
                    "from_question_id": previous["question_id"],
                    "to_question_id": current["question_id"],
                    "knowledge_delta": round(delta, 2),
                }
            )

        overall_score = _extract_overall_score(report, summary)
        speech_total = _extract_speech_score(report, summary)
        knowledge_total = _extract_knowledge_score(report, summary)

        follow_up_count = len([q for q in questions if q.is_follow_up])

        return {
            "interview_id": interview.id,
            "role_selected": interview.track,
            "difficulty": interview.difficulty,
            "status": interview.status,
            "total_score": round(overall_score, 2) if overall_score is not None else None,
            "speech_score": round(speech_total, 2) if speech_total is not None else None,
            "knowledge_score": round(knowledge_total, 2) if knowledge_total is not None else None,
            "duration_seconds": duration_seconds,
            "completion_rate": round(completion_rate, 2),
            "question_level": question_items,
            "follow_up_analytics": {
                "follow_up_triggered": follow_up_count > 0,
                "follow_up_count": follow_up_count,
                "follow_up_percentage_of_questions": round((follow_up_count / total_questions) * 100.0, 2) if total_questions > 0 else 0.0,
                "follow_up_performance_delta": follow_up_delta,
                "question_level_dropoff": question_dropoff,
            },
        }

    async def get_role_segment_analytics(
        self,
        *,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
        role: str | None = None,
        difficulty: str | None = None,
        college: str | None = None,
    ) -> list[dict[str, Any]]:
        interviews = await self._list_interviews_all(start_date=start_date, end_date=end_date, role=role, difficulty=difficulty, college=college)
        if not interviews:
            return []
        reports = await self._reports_by_interview([i.id for i in interviews])
        summaries = await self._summary_reports_by_interview([i.id for i in interviews])

        grouped: dict[str, list[Interview]] = defaultdict(list)
        for interview in interviews:
            grouped[interview.track or "unknown"].append(interview)

        output: list[dict[str, Any]] = []
        for role_name, role_interviews in grouped.items():
            scores = [
                _extract_overall_score(reports.get(i.id), summaries.get(i.id))
                for i in role_interviews
            ]
            scores_clean = [s for s in scores if s is not None]
            completed = len([i for i in role_interviews if i.status == "completed"])
            avg_duration = await self._average_interview_duration_seconds([i.id for i in role_interviews])

            weak_tags = await self._common_weakness_tags([i.id for i in role_interviews])

            output.append(
                {
                    "role": role_name,
                    "interviews": len(role_interviews),
                    "avg_score": round(_avg_non_null(scores_clean), 2) if scores_clean else None,
                    "drop_off_rate": round((1 - (completed / len(role_interviews))) * 100.0, 2) if role_interviews else 0.0,
                    "common_weaknesses": weak_tags,
                    "avg_time_spent_seconds": avg_duration,
                }
            )

        return sorted(output, key=lambda x: x.get("avg_score") if x.get("avg_score") is not None else -1, reverse=True)

    async def get_difficulty_segment_analytics(
        self,
        *,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
        role: str | None = None,
        difficulty: str | None = None,
        college: str | None = None,
    ) -> list[dict[str, Any]]:
        interviews = await self._list_interviews_all(start_date=start_date, end_date=end_date, role=role, difficulty=difficulty, college=college)
        reports = await self._reports_by_interview([i.id for i in interviews])
        summaries = await self._summary_reports_by_interview([i.id for i in interviews])
        grouped: dict[str, list[Interview]] = defaultdict(list)
        for interview in interviews:
            grouped[interview.difficulty or "unknown"].append(interview)

        output: list[dict[str, Any]] = []
        for level, level_interviews in grouped.items():
            scores = [_extract_overall_score(reports.get(i.id), summaries.get(i.id)) for i in level_interviews]
            clean_scores = [s for s in scores if s is not None]
            completed = len([i for i in level_interviews if i.status == "completed"])
            retry_rate = await self._retry_rate_for_interviews([i.id for i in level_interviews])
            output.append(
                {
                    "difficulty": level,
                    "interviews": len(level_interviews),
                    "avg_score": round(_avg_non_null(clean_scores), 2) if clean_scores else None,
                    "completion_rate": round((completed / len(level_interviews)) * 100.0, 2) if level_interviews else 0.0,
                    "retry_rate": retry_rate,
                }
            )
        return sorted(output, key=lambda x: x.get("difficulty") or "")

    async def get_college_segment_analytics(
        self,
        *,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
        role: str | None = None,
        difficulty: str | None = None,
        college: str | None = None,
    ) -> list[dict[str, Any]]:
        interviews = await self._list_interviews_all(start_date=start_date, end_date=end_date, role=role, difficulty=difficulty, college=college)
        reports = await self._reports_by_interview([i.id for i in interviews])
        summaries = await self._summary_reports_by_interview([i.id for i in interviews])

        users_stmt = sqlalchemy.select(User)
        users = list((await self._db.execute(users_stmt)).scalars().all())
        user_map = {u.id: u for u in users}

        grouped: dict[str, list[Interview]] = defaultdict(list)
        for interview in interviews:
            user = user_map.get(interview.user_id)
            key = (user.university if user and user.university else "unknown")
            grouped[key].append(interview)

        output: list[dict[str, Any]] = []
        for college_name, college_interviews in grouped.items():
            scores = [_extract_overall_score(reports.get(i.id), summaries.get(i.id)) for i in college_interviews]
            clean_scores = [s for s in scores if s is not None]
            completed = len([i for i in college_interviews if i.status == "completed"])

            sorted_scored = sorted(
                [(_extract_overall_score(reports.get(i.id), summaries.get(i.id)), i.created_at) for i in college_interviews],
                key=lambda x: x[1] if x[1] is not None else datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
            )
            first = next((x[0] for x in sorted_scored if x[0] is not None), None)
            latest = next((x[0] for x in reversed(sorted_scored) if x[0] is not None), None)

            output.append(
                {
                    "college": college_name,
                    "interviews": len(college_interviews),
                    "avg_score": round(_avg_non_null(clean_scores), 2) if clean_scores else None,
                    "improvement_rate": round((latest - first), 2) if latest is not None and first is not None else None,
                    "usage_frequency": len({i.user_id for i in college_interviews}),
                    "completion_rate": round((completed / len(college_interviews)) * 100.0, 2) if college_interviews else 0.0,
                }
            )

        return sorted(output, key=lambda x: x.get("avg_score") if x.get("avg_score") is not None else -1, reverse=True)

    async def get_system_analytics(
        self,
        *,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
        role: str | None = None,
        difficulty: str | None = None,
        college: str | None = None,
    ) -> dict[str, Any]:
        interviews = await self._list_interviews_all(start_date=start_date, end_date=end_date, role=role, difficulty=difficulty, college=college)
        interview_ids = [i.id for i in interviews]
        scoped_user_ids = {i.user_id for i in interviews}
        reports = await self._reports_by_interview(interview_ids)
        summaries = await self._summary_reports_by_interview(interview_ids)
        users_stmt = sqlalchemy.select(User)
        users = list((await self._db.execute(users_stmt)).scalars().all())

        events = await self._list_analytics_events(
            start_date=start_date,
            end_date=end_date,
            role=role,
            difficulty=difficulty,
            college=college,
        )

        now = datetime.datetime.now(datetime.timezone.utc)
        active_cutoff = now - datetime.timedelta(days=30)

        active_user_ids = {i.user_id for i in interviews if i.created_at and i.created_at >= active_cutoff}
        avg_scores = [
            _extract_overall_score(reports.get(i.id), summaries.get(i.id))
            for i in interviews
        ]
        avg_scores_clean = [x for x in avg_scores if x is not None]

        user_ids_started = {e.user_id for e in events if e.event_type == "interview_started" and e.user_id is not None}
        if not user_ids_started:
            user_ids_started = {i.user_id for i in interviews}

        user_ids_completed = {e.user_id for e in events if e.event_type == "interview_completed" and e.user_id is not None}
        if not user_ids_completed:
            user_ids_completed = {i.user_id for i in interviews if i.status == "completed"}

        user_ids_with_report = {e.user_id for e in events if e.event_type == "report_viewed" and e.user_id is not None}
        if not user_ids_with_report:
            user_ids_with_report = {i.user_id for i in interviews if i.id in reports or i.id in summaries}

        user_ids_practice = await self._users_with_practice()

        user_ids_role_selected = {e.user_id for e in events if e.event_type == "role_selected" and e.user_id is not None}
        if not user_ids_role_selected:
            user_ids_role_selected = {u.id for u in users if u.target_position}

        funnel = {
            "sign_up": len(users),
            "select_role": len(user_ids_role_selected),
            "start_interview": len(user_ids_started),
            "complete_interview": len(user_ids_completed),
            "view_report": len(user_ids_with_report),
            "do_practice": len(user_ids_practice),
        }

        funnel_drop_off = {
            "signup_to_role_selection_dropoff": _dropoff(funnel["sign_up"], funnel["select_role"]),
            "role_selection_to_start_interview_dropoff": _dropoff(funnel["select_role"], funnel["start_interview"]),
            "start_to_complete_dropoff": _dropoff(funnel["start_interview"], funnel["complete_interview"]),
            "complete_to_report_dropoff": _dropoff(funnel["complete_interview"], funnel["view_report"]),
            "report_to_practice_dropoff": _dropoff(funnel["view_report"], funnel["do_practice"]),
        }

        try:
            practice_effectiveness = await self._practice_effectiveness(user_ids=scoped_user_ids)
        except TypeError:
            practice_effectiveness = await self._practice_effectiveness()

        try:
            retry_behavior = await self._system_retry_behavior(interview_ids=interview_ids)
        except TypeError:
            retry_behavior = await self._system_retry_behavior()
        question_effectiveness = await self._question_effectiveness(interview_ids)

        report_engagement_events = [e for e in events if e.event_type == "report_engagement"]
        total_time_spent = 0
        total_clicks = 0
        for event in report_engagement_events:
            data = event.event_data or {}
            total_time_spent += int(_to_float(data.get("time_spent_seconds")) or 0)
            total_clicks += int(_to_float(data.get("recommendation_clicks")) or 0)

        return {
            "overview": {
                "total_users": len(users),
                "active_users_30d": len(active_user_ids),
                "avg_score": round(_avg_non_null(avg_scores_clean), 2) if avg_scores_clean else None,
                "improvement_percent": round(_improvement_percent_from_interviews(interviews, reports, summaries), 2),
            },
            "funnel": funnel,
            "funnel_dropoff": funnel_drop_off,
            "report_usage": {
                "percent_users_open_report": round((funnel["view_report"] / funnel["start_interview"] * 100.0), 2)
                if funnel["start_interview"]
                else 0.0,
                "time_spent_on_report_seconds": total_time_spent if total_time_spent > 0 else None,
                "recommendation_clicks": total_clicks if total_clicks > 0 else None,
            },
            "practice_effectiveness": practice_effectiveness,
            "retry_behavior": retry_behavior,
            "question_effectiveness": question_effectiveness,
        }

    async def get_scoring_analytics(
        self,
        *,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
        role: str | None = None,
        difficulty: str | None = None,
        college: str | None = None,
    ) -> dict[str, Any]:
        interviews = await self._list_interviews_all(start_date=start_date, end_date=end_date, role=role, difficulty=difficulty, college=college)
        reports = await self._reports_by_interview([i.id for i in interviews])
        summaries = await self._summary_reports_by_interview([i.id for i in interviews])

        speech: list[float] = []
        knowledge: list[float] = []
        overall: list[float] = []

        for interview in interviews:
            report = reports.get(interview.id)
            summary = summaries.get(interview.id)
            s = _extract_speech_score(report, summary)
            k = _extract_knowledge_score(report, summary)
            o = _extract_overall_score(report, summary)
            if s is not None:
                speech.append(s)
            if k is not None:
                knowledge.append(k)
            if o is not None:
                overall.append(o)

        correlation = _pearson(speech, knowledge)
        distribution = _histogram(overall, bins=[0, 20, 40, 60, 80, 100])
        suspicious_range = _is_distribution_too_narrow(overall)

        return {
            "correlation": {
                "speech_vs_knowledge": round(correlation, 4) if correlation is not None else None,
            },
            "score_distribution": distribution,
            "scoring_health": {
                "n_samples": len(overall),
                "is_too_narrow": suspicious_range,
                "note": "If most scores cluster in a narrow range (for example 70-80), scoring calibration may need review.",
            },
        }

    async def get_alerts(
        self,
        *,
        user_id: int | None = None,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
    ) -> dict[str, Any]:
        system_alerts: list[dict[str, Any]] = []

        users = await self._candidate_users(user_id=user_id)
        candidate_user_ids = [u.id for u in users]
        student_alerts = await self._student_alerts_batched(
            user_ids=candidate_user_ids,
            start_date=start_date,
            end_date=end_date,
        )

        # System-level alerts
        now = datetime.datetime.now(datetime.timezone.utc)
        current_window_start = (now - datetime.timedelta(days=7)).date()
        prev_window_start = (now - datetime.timedelta(days=14)).date()
        prev_window_end = (now - datetime.timedelta(days=8)).date()

        current_scores = await self._overall_scores_between(current_window_start, now.date())
        previous_scores = await self._overall_scores_between(prev_window_start, prev_window_end)

        current_avg = _avg_non_null(current_scores)
        prev_avg = _avg_non_null(previous_scores)
        if current_avg is not None and prev_avg is not None and (prev_avg - current_avg) >= 15:
            system_alerts.append(
                {
                    "type": "SUDDEN_SCORE_DROP",
                    "message": "Average scores dropped significantly in the last 7 days.",
                    "previous_avg": round(prev_avg, 2),
                    "current_avg": round(current_avg, 2),
                }
            )

        interviews_all = await self._list_interviews_all()
        reports_all = await self._reports_by_interview([i.id for i in interviews_all])
        summaries_all = await self._summary_reports_by_interview([i.id for i in interviews_all])
        user_university_map = await self._user_university_map()

        role_groups: dict[str, list[Interview]] = defaultdict(list)
        for interview in interviews_all:
            role_groups[(interview.track or "unknown").strip() or "unknown"].append(interview)
        for role_name, role_interviews in role_groups.items():
            total = len(role_interviews)
            completed = len([item for item in role_interviews if item.status == "completed"])
            drop_off_rate = ((total - completed) / total * 100.0) if total else 0.0
            if total >= 10 and drop_off_rate > 60:
                system_alerts.append(
                    {
                        "type": "HIGH_FAILURE_RATE_ROLE",
                        "role": role_name,
                        "message": "High drop-off/failure rate detected for role.",
                        "drop_off_rate": round(drop_off_rate, 2),
                    }
                )

        college_score_groups: dict[str, list[float]] = defaultdict(list)
        college_interview_counts: dict[str, int] = defaultdict(int)
        for interview in interviews_all:
            college_name = (user_university_map.get(interview.user_id) or "unknown").strip() or "unknown"
            college_interview_counts[college_name] += 1
            score = _extract_overall_score(reports_all.get(interview.id), summaries_all.get(interview.id))
            if score is not None:
                college_score_groups[college_name].append(score)

        for college_name, total in college_interview_counts.items():
            avg_score = _avg_non_null(college_score_groups.get(college_name, []))
            if total >= 10 and avg_score is not None and avg_score < 40:
                system_alerts.append(
                    {
                        "type": "COLLEGE_PERFORMING_LOW",
                        "college": college_name,
                        "message": "College segment average score is critically low.",
                        "avg_score": round(avg_score, 2),
                    }
                )

        return {
            "student_alerts": student_alerts,
            "system_alerts": system_alerts,
        }

    async def _list_interviews(
        self,
        *,
        user_id: int,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
    ) -> list[Interview]:
        stmt = sqlalchemy.select(Interview).where(Interview.user_id == user_id).order_by(Interview.created_at.asc())
        if start_date is not None:
            stmt = stmt.where(Interview.created_at >= datetime.datetime.combine(start_date, datetime.time.min, tzinfo=datetime.timezone.utc))
        if end_date is not None:
            stmt = stmt.where(Interview.created_at <= datetime.datetime.combine(end_date, datetime.time.max, tzinfo=datetime.timezone.utc))
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def _list_interviews_all(
        self,
        *,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
        role: str | None = None,
        difficulty: str | None = None,
        college: str | None = None,
    ) -> list[Interview]:
        stmt = sqlalchemy.select(Interview)
        if college:
            stmt = stmt.join(User, User.id == Interview.user_id).where(User.university == college)
        if role:
            stmt = stmt.where(Interview.track == role)
        if difficulty:
            stmt = stmt.where(Interview.difficulty == difficulty)
        if start_date is not None:
            stmt = stmt.where(Interview.created_at >= datetime.datetime.combine(start_date, datetime.time.min, tzinfo=datetime.timezone.utc))
        if end_date is not None:
            stmt = stmt.where(Interview.created_at <= datetime.datetime.combine(end_date, datetime.time.max, tzinfo=datetime.timezone.utc))
        stmt = stmt.order_by(Interview.created_at.asc())
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def _reports_by_interview(self, interview_ids: list[int]) -> dict[int, Report]:
        if not interview_ids:
            return {}
        stmt = sqlalchemy.select(Report).where(Report.interview_id.in_(interview_ids))
        rows = list((await self._db.execute(stmt)).scalars().all())
        return {row.interview_id: row for row in rows}

    async def _summary_reports_by_interview(self, interview_ids: list[int]) -> dict[int, SummaryReport]:
        if not interview_ids:
            return {}
        stmt = sqlalchemy.select(SummaryReport).where(SummaryReport.interview_id.in_(interview_ids))
        rows = list((await self._db.execute(stmt)).scalars().all())
        return {row.interview_id: row for row in rows}

    async def _attempts_for_interviews(self, interview_ids: list[int]) -> list[QuestionAttempt]:
        if not interview_ids:
            return []
        stmt = sqlalchemy.select(QuestionAttempt).where(QuestionAttempt.interview_id.in_(interview_ids))
        return list((await self._db.execute(stmt)).scalars().all())

    async def _questions_for_interviews(self, interview_ids: list[int]) -> list[InterviewQuestion]:
        if not interview_ids:
            return []
        stmt = sqlalchemy.select(InterviewQuestion).where(InterviewQuestion.interview_id.in_(interview_ids))
        return list((await self._db.execute(stmt)).scalars().all())

    async def _reattempt_stats(
        self,
        *,
        user_id: int,
        start_date: datetime.date | None,
        end_date: datetime.date | None,
    ) -> dict[str, Any]:
        stmt = (
            sqlalchemy.select(
                QuestionAttempt.interview_id,
                QuestionAttempt.question_id,
                sqlalchemy.func.count(QuestionAttempt.id).label("attempt_count"),
            )
            .join(Interview, Interview.id == QuestionAttempt.interview_id)
            .where(Interview.user_id == user_id)
            .group_by(QuestionAttempt.interview_id, QuestionAttempt.question_id)
        )
        if start_date is not None:
            stmt = stmt.where(Interview.created_at >= datetime.datetime.combine(start_date, datetime.time.min, tzinfo=datetime.timezone.utc))
        if end_date is not None:
            stmt = stmt.where(Interview.created_at <= datetime.datetime.combine(end_date, datetime.time.max, tzinfo=datetime.timezone.utc))

        rows = list((await self._db.execute(stmt)).all())
        total_questions = len(rows)
        reattempted = len([r for r in rows if int(r.attempt_count) > 1])
        return {
            "questions_with_attempts": total_questions,
            "questions_reattempted": reattempted,
            "reattempt_ratio": round((reattempted / total_questions), 4) if total_questions else 0.0,
        }

    async def _practice_compliance(self, *, user_id: int) -> dict[str, Any]:
        summary_stmt = (
            sqlalchemy.select(SummaryReport)
            .join(Interview, Interview.id == SummaryReport.interview_id)
            .where(Interview.user_id == user_id)
        )
        summary_reports = list((await self._db.execute(summary_stmt)).scalars().all())
        recommended_count = 0
        for summary in summary_reports:
            report_json = summary.report_json or {}
            if isinstance(report_json, dict) and report_json.get("recommendedPractice"):
                recommended_count += 1

        pronunciation_count = await self._count_rows(PronunciationPractice, user_id)
        pacing_count = await self._count_rows(PacingPracticeSession, user_id)
        structure_count = await self._count_rows(StructurePractice, user_id)
        structure_answers_count = await self._count_structure_answers(user_id)

        completed_exercises = pronunciation_count + pacing_count + structure_count + structure_answers_count
        completion_ratio = (completed_exercises / recommended_count) if recommended_count > 0 else None

        improvement_after_practice = await self._improvement_after_practice(user_id)

        return {
            "recommended_exercises": recommended_count,
            "completed_exercises": completed_exercises,
            "completion_ratio": round(completion_ratio, 4) if completion_ratio is not None else None,
            "improvement_after_practice": improvement_after_practice,
        }

    async def _count_rows(self, model: Any, user_id: int) -> int:
        stmt = sqlalchemy.select(sqlalchemy.func.count()).select_from(model).where(model.user_id == user_id)
        result = await self._db.execute(stmt)
        return int(result.scalar() or 0)

    async def _count_structure_answers(self, user_id: int) -> int:
        stmt = (
            sqlalchemy.select(sqlalchemy.func.count(StructurePracticeAnswer.id))
            .join(StructurePractice, StructurePractice.id == StructurePracticeAnswer.practice_id)
            .where(StructurePractice.user_id == user_id)
        )
        result = await self._db.execute(stmt)
        return int(result.scalar() or 0)

    async def _improvement_after_practice(self, user_id: int) -> dict[str, Any]:
        earliest_practice = await self._earliest_practice_timestamp(user_id)
        interviews = await self._list_interviews(user_id=user_id)
        if not interviews or earliest_practice is None:
            return {
                "available": False,
                "delta": None,
            }

        reports = await self._reports_by_interview([i.id for i in interviews])
        summaries = await self._summary_reports_by_interview([i.id for i in interviews])
        pre_scores: list[float] = []
        post_scores: list[float] = []
        for interview in interviews:
            score = _extract_overall_score(reports.get(interview.id), summaries.get(interview.id))
            if score is None:
                continue
            if interview.created_at and interview.created_at < earliest_practice:
                pre_scores.append(score)
            else:
                post_scores.append(score)

        if not pre_scores or not post_scores:
            return {"available": False, "delta": None}

        delta = _avg_non_null(post_scores) - _avg_non_null(pre_scores)
        return {
            "available": True,
            "pre_practice_avg": round(_avg_non_null(pre_scores), 2),
            "post_practice_avg": round(_avg_non_null(post_scores), 2),
            "delta": round(delta, 2),
        }

    async def _earliest_practice_timestamp(self, user_id: int) -> datetime.datetime | None:
        timestamps: list[datetime.datetime] = []
        for model in (PronunciationPractice, PacingPracticeSession, StructurePractice):
            stmt = sqlalchemy.select(sqlalchemy.func.min(model.created_at)).where(model.user_id == user_id)
            ts = (await self._db.execute(stmt)).scalar()
            if ts is not None:
                timestamps.append(ts)
        return min(timestamps) if timestamps else None

    async def _average_interview_duration_seconds(self, interview_ids: list[int]) -> int | None:
        if not interview_ids:
            return None
        stmt = (
            sqlalchemy.select(
                QuestionAttempt.interview_id,
                sqlalchemy.func.min(QuestionAttempt.created_at).label("start_time"),
                sqlalchemy.func.max(QuestionAttempt.created_at).label("end_time"),
            )
            .where(QuestionAttempt.interview_id.in_(interview_ids))
            .group_by(QuestionAttempt.interview_id)
        )
        rows = list((await self._db.execute(stmt)).all())
        durations: list[int] = []
        for row in rows:
            if row.start_time is None or row.end_time is None:
                continue
            durations.append(max(0, int((row.end_time - row.start_time).total_seconds())))
        if not durations:
            return None
        return int(sum(durations) / len(durations))

    async def _common_weakness_tags(self, interview_ids: list[int]) -> list[str]:
        if not interview_ids:
            return []
        stmt = sqlalchemy.select(QuestionAttempt).where(QuestionAttempt.interview_id.in_(interview_ids))
        attempts = list((await self._db.execute(stmt)).scalars().all())
        weakness_counts: dict[str, int] = defaultdict(int)
        for attempt in attempts:
            analysis = attempt.analysis_json or {}
            communication = analysis.get("communication") or {}
            domain = analysis.get("domain") or {}
            for tag in _as_str_list(communication.get("improvements")) + _as_str_list(communication.get("recommendations")) + _as_str_list(domain.get("improvements")):
                normalized = tag.strip().lower()
                if normalized:
                    weakness_counts[normalized] += 1
        sorted_tags = sorted(weakness_counts.items(), key=lambda x: x[1], reverse=True)
        return [tag for tag, _ in sorted_tags[:5]]

    async def _retry_rate_for_interviews(self, interview_ids: list[int]) -> float:
        if not interview_ids:
            return 0.0
        stmt = (
            sqlalchemy.select(QuestionAttempt.interview_id, QuestionAttempt.question_id, sqlalchemy.func.count(QuestionAttempt.id).label("attempt_count"))
            .where(QuestionAttempt.interview_id.in_(interview_ids))
            .group_by(QuestionAttempt.interview_id, QuestionAttempt.question_id)
        )
        rows = list((await self._db.execute(stmt)).all())
        total_questions = len(rows)
        retries = len([r for r in rows if int(r.attempt_count) > 1])
        return round((retries / total_questions) * 100.0, 2) if total_questions else 0.0

    async def _users_with_practice(self) -> set[int]:
        user_ids: set[int] = set()
        for model in (PronunciationPractice, PacingPracticeSession, StructurePractice):
            stmt = sqlalchemy.select(model.user_id).distinct()
            rows = list((await self._db.execute(stmt)).all())
            user_ids.update(int(r[0]) for r in rows if r and r[0] is not None)
        return user_ids

    async def _practice_effectiveness(self, *, user_ids: set[int] | None = None) -> dict[str, Any]:
        if user_ids is not None:
            candidate_user_ids = [int(uid) for uid in user_ids if uid is not None]
        else:
            users = list((await self._db.execute(sqlalchemy.select(User.id))).all())
            candidate_user_ids = [int(r[0]) for r in users if r and r[0] is not None]

        if not candidate_user_ids:
            return {
                "users_with_measurable_practice_effect": 0,
                "avg_score_delta_after_practice": None,
                "positive_improvement_rate": None,
            }

        earliest_practice = await self._earliest_practice_by_users(candidate_user_ids)
        if not earliest_practice:
            return {
                "users_with_measurable_practice_effect": 0,
                "avg_score_delta_after_practice": None,
                "positive_improvement_rate": None,
            }

        interview_stmt = sqlalchemy.select(Interview).where(Interview.user_id.in_(candidate_user_ids))
        interviews = list((await self._db.execute(interview_stmt)).scalars().all())
        reports = await self._reports_by_interview([i.id for i in interviews])
        summaries = await self._summary_reports_by_interview([i.id for i in interviews])

        pre_scores_by_user: dict[int, list[float]] = defaultdict(list)
        post_scores_by_user: dict[int, list[float]] = defaultdict(list)
        for interview in interviews:
            practice_ts = earliest_practice.get(interview.user_id)
            if practice_ts is None:
                continue
            score = _extract_overall_score(reports.get(interview.id), summaries.get(interview.id))
            if score is None:
                continue
            if interview.created_at and interview.created_at < practice_ts:
                pre_scores_by_user[interview.user_id].append(score)
            else:
                post_scores_by_user[interview.user_id].append(score)

        deltas: list[float] = []
        contributing_users = 0
        for uid in candidate_user_ids:
            pre_scores = pre_scores_by_user.get(uid, [])
            post_scores = post_scores_by_user.get(uid, [])
            if not pre_scores or not post_scores:
                continue
            delta = _avg_non_null(post_scores) - _avg_non_null(pre_scores)
            deltas.append(float(delta))
            contributing_users += 1

        return {
            "users_with_measurable_practice_effect": contributing_users,
            "avg_score_delta_after_practice": round(_avg_non_null(deltas), 2) if deltas else None,
            "positive_improvement_rate": round((len([d for d in deltas if d > 0]) / len(deltas)) * 100.0, 2) if deltas else None,
        }

    async def _system_retry_behavior(self, *, interview_ids: list[int] | None = None) -> dict[str, Any]:
        stmt = (
            sqlalchemy.select(QuestionAttempt.interview_id, QuestionAttempt.question_id, sqlalchemy.func.count(QuestionAttempt.id).label("attempt_count"))
            .group_by(QuestionAttempt.interview_id, QuestionAttempt.question_id)
        )
        if interview_ids is not None:
            if not interview_ids:
                return {
                    "avg_retries_before_completion": 0.0,
                    "high_retry_questions": 0,
                }
            stmt = stmt.where(QuestionAttempt.interview_id.in_(interview_ids))
        rows = list((await self._db.execute(stmt)).all())
        retries = [int(r.attempt_count) - 1 for r in rows if int(r.attempt_count) > 1]
        return {
            "avg_retries_before_completion": round(_avg_non_null(retries), 2) if retries else 0.0,
            "high_retry_questions": len([x for x in retries if x >= 2]),
        }

    async def _earliest_practice_by_users(self, user_ids: list[int]) -> dict[int, datetime.datetime]:
        if not user_ids:
            return {}

        result_map: dict[int, datetime.datetime] = {}
        for model in (PronunciationPractice, PacingPracticeSession, StructurePractice):
            stmt = (
                sqlalchemy.select(model.user_id, sqlalchemy.func.min(model.created_at))
                .where(model.user_id.in_(user_ids))
                .group_by(model.user_id)
            )
            rows = list((await self._db.execute(stmt)).all())
            for row in rows:
                uid = int(row[0])
                ts = row[1]
                if ts is None:
                    continue
                existing = result_map.get(uid)
                if existing is None or ts < existing:
                    result_map[uid] = ts

        return result_map

    async def _user_university_map(self) -> dict[int, str | None]:
        rows = list((await self._db.execute(sqlalchemy.select(User.id, User.university))).all())
        return {int(row[0]): row[1] for row in rows if row and row[0] is not None}

    async def _student_alerts_batched(
        self,
        *,
        user_ids: list[int],
        start_date: datetime.date | None,
        end_date: datetime.date | None,
    ) -> list[dict[str, Any]]:
        if not user_ids:
            return []

        interview_stmt = sqlalchemy.select(Interview).where(Interview.user_id.in_(user_ids))
        if start_date is not None:
            interview_stmt = interview_stmt.where(Interview.created_at >= datetime.datetime.combine(start_date, datetime.time.min, tzinfo=datetime.timezone.utc))
        if end_date is not None:
            interview_stmt = interview_stmt.where(Interview.created_at <= datetime.datetime.combine(end_date, datetime.time.max, tzinfo=datetime.timezone.utc))
        interviews = list((await self._db.execute(interview_stmt)).scalars().all())
        interviews_by_user: dict[int, list[Interview]] = defaultdict(list)
        for interview in interviews:
            interviews_by_user[interview.user_id].append(interview)

        reports = await self._reports_by_interview([i.id for i in interviews])
        summaries = await self._summary_reports_by_interview([i.id for i in interviews])

        improvement_by_user: dict[int, float | None] = {}
        scored_count_by_user: dict[int, int] = defaultdict(int)
        for uid, user_interviews in interviews_by_user.items():
            scored: list[tuple[datetime.datetime, float]] = []
            for interview in user_interviews:
                score = _extract_overall_score(reports.get(interview.id), summaries.get(interview.id))
                if score is None:
                    continue
                scored.append((interview.created_at or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), score))
            scored.sort(key=lambda x: x[0])
            scored_count_by_user[uid] = len(scored)
            if len(scored) >= 2:
                improvement_by_user[uid] = scored[-1][1] - scored[0][1]
            else:
                improvement_by_user[uid] = None

        retry_stmt = (
            sqlalchemy.select(
                Interview.user_id,
                QuestionAttempt.question_id,
                sqlalchemy.func.count(QuestionAttempt.id).label("attempt_count"),
            )
            .join(Interview, Interview.id == QuestionAttempt.interview_id)
            .where(Interview.user_id.in_(user_ids))
            .group_by(Interview.user_id, QuestionAttempt.question_id)
        )
        if start_date is not None:
            retry_stmt = retry_stmt.where(Interview.created_at >= datetime.datetime.combine(start_date, datetime.time.min, tzinfo=datetime.timezone.utc))
        if end_date is not None:
            retry_stmt = retry_stmt.where(Interview.created_at <= datetime.datetime.combine(end_date, datetime.time.max, tzinfo=datetime.timezone.utc))

        retry_rows = list((await self._db.execute(retry_stmt)).all())
        total_questions_by_user: dict[int, int] = defaultdict(int)
        reattempted_by_user: dict[int, int] = defaultdict(int)
        for row in retry_rows:
            uid = int(row.user_id)
            total_questions_by_user[uid] += 1
            if int(row.attempt_count) > 1:
                reattempted_by_user[uid] += 1

        weak_stmt = (
            sqlalchemy.select(Interview.user_id, QuestionAttempt.analysis_json)
            .join(Interview, Interview.id == QuestionAttempt.interview_id)
            .where(Interview.user_id.in_(user_ids))
        )
        if start_date is not None:
            weak_stmt = weak_stmt.where(Interview.created_at >= datetime.datetime.combine(start_date, datetime.time.min, tzinfo=datetime.timezone.utc))
        if end_date is not None:
            weak_stmt = weak_stmt.where(Interview.created_at <= datetime.datetime.combine(end_date, datetime.time.max, tzinfo=datetime.timezone.utc))
        weak_rows = list((await self._db.execute(weak_stmt)).all())

        filler_values_by_user: dict[int, list[float]] = defaultdict(list)
        energy_values_by_user: dict[int, list[float]] = defaultdict(list)
        for row in weak_rows:
            uid = int(row.user_id)
            analysis = row.analysis_json or {}
            if not isinstance(analysis, dict):
                continue
            communication = analysis.get("communication") or {}
            filler_density = _to_float(
                communication.get("filler_density")
                or communication.get("filler_rate")
                or communication.get("filler_percentage")
            )
            energy = _normalize_score(_to_float(communication.get("energy") or communication.get("energy_score")))
            if filler_density is not None:
                filler_values_by_user[uid].append(filler_density)
            if energy is not None:
                energy_values_by_user[uid].append(energy)

        student_alerts: list[dict[str, Any]] = []
        for uid in user_ids:
            improvement = improvement_by_user.get(uid)
            score_history_len = int(scored_count_by_user.get(uid, 0))
            if score_history_len >= 3 and (improvement is None or improvement <= 0):
                student_alerts.append(
                    {
                        "type": "NO_IMPROVEMENT_AFTER_3_ATTEMPTS",
                        "user_id": uid,
                        "message": "No improvement after at least 3 interviews.",
                    }
                )

            avg_filler = _avg_non_null(filler_values_by_user.get(uid, []))
            avg_energy = _avg_non_null(energy_values_by_user.get(uid, []))
            if avg_filler is not None and avg_energy is not None and avg_filler > 0.08 and avg_energy < 45:
                student_alerts.append(
                    {
                        "type": "HIGH_FILLER_LOW_ENERGY",
                        "user_id": uid,
                        "message": "High filler usage and low energy detected.",
                    }
                )

            total_questions = int(total_questions_by_user.get(uid, 0))
            retry_ratio = (reattempted_by_user.get(uid, 0) / total_questions) if total_questions else 0.0
            if retry_ratio >= 0.4 and (improvement is None or improvement <= 0):
                student_alerts.append(
                    {
                        "type": "TOO_MANY_RETRIES_WITHOUT_PROGRESS",
                        "user_id": uid,
                        "message": "High reattempt frequency without measurable score improvement.",
                    }
                )

        return student_alerts

    async def _question_effectiveness(self, interview_ids: list[int]) -> dict[str, Any]:
        if not interview_ids:
            return {"low_score_questions": [], "high_dropoff_questions": []}

        questions_stmt = sqlalchemy.select(InterviewQuestion).where(InterviewQuestion.interview_id.in_(interview_ids))
        attempts_stmt = sqlalchemy.select(QuestionAttempt).where(QuestionAttempt.interview_id.in_(interview_ids))

        questions = list((await self._db.execute(questions_stmt)).scalars().all())
        attempts = list((await self._db.execute(attempts_stmt)).scalars().all())

        latest_by_question: dict[int, QuestionAttempt] = {}
        for a in sorted(attempts, key=lambda x: x.id, reverse=True):
            if a.question_id is not None and a.question_id not in latest_by_question:
                latest_by_question[a.question_id] = a

        question_scores: dict[int, float] = {}
        for q in questions:
            qa = latest_by_question.get(q.id)
            if qa is None:
                continue
            analysis = qa.analysis_json or {}
            domain = analysis.get("domain") or {}
            comm = analysis.get("communication") or {}
            knowledge = _normalize_score(_to_float(((domain.get("criteria") or {}).get("correctness") or {}).get("score") or domain.get("domain_score")))
            speech = _normalize_score(_to_float(comm.get("communication_score") or comm.get("overall_score")))
            combined = _avg_non_null([knowledge, speech])
            if combined is not None:
                question_scores[q.id] = combined

        low_score_questions = sorted(
            [
                {
                    "question_id": q.id,
                    "question_text": q.text,
                    "question_type": _question_type(q.category),
                    "score": round(question_scores[q.id], 2),
                }
                for q in questions
                if q.id in question_scores
            ],
            key=lambda x: x["score"],
        )[:10]

        attempt_question_ids = {a.question_id for a in attempts if a.question_id is not None}
        high_dropoff_questions = [
            {
                "question_id": q.id,
                "question_text": q.text,
                "question_type": _question_type(q.category),
            }
            for q in questions
            if q.id not in attempt_question_ids
        ][:10]

        return {
            "low_score_questions": low_score_questions,
            "high_dropoff_questions": high_dropoff_questions,
        }

    async def _global_improvement_percent(self) -> float:
        users = list((await self._db.execute(sqlalchemy.select(User.id))).all())
        improvements: list[float] = []
        for row in users:
            user_id = int(row[0])
            interviews = await self._list_interviews(user_id=user_id)
            if len(interviews) < 2:
                continue
            reports = await self._reports_by_interview([i.id for i in interviews])
            summaries = await self._summary_reports_by_interview([i.id for i in interviews])
            scored = [
                (_extract_overall_score(reports.get(i.id), summaries.get(i.id)), i.created_at)
                for i in interviews
            ]
            scored = [x for x in scored if x[0] is not None]
            if len(scored) < 2:
                continue
            scored.sort(key=lambda x: x[1] if x[1] is not None else datetime.datetime.min.replace(tzinfo=datetime.timezone.utc))
            first = scored[0][0]
            latest = scored[-1][0]
            if first is not None and latest is not None:
                improvements.append(latest - first)
        return _avg_non_null(improvements) if improvements else 0.0

    async def _candidate_users(self, *, user_id: int | None) -> list[User]:
        stmt = sqlalchemy.select(User)
        if user_id is not None:
            stmt = stmt.where(User.id == user_id)
        users = list((await self._db.execute(stmt)).scalars().all())
        return users

    async def _overall_scores_between(self, start_date: datetime.date, end_date: datetime.date) -> list[float]:
        interviews = await self._list_interviews_all(start_date=start_date, end_date=end_date)
        reports = await self._reports_by_interview([i.id for i in interviews])
        summaries = await self._summary_reports_by_interview([i.id for i in interviews])
        scores = [_extract_overall_score(reports.get(i.id), summaries.get(i.id)) for i in interviews]
        return [s for s in scores if s is not None]

    async def _list_analytics_events(
        self,
        *,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
        role: str | None = None,
        difficulty: str | None = None,
        college: str | None = None,
    ) -> list[AnalyticsEvent]:
        stmt = sqlalchemy.select(AnalyticsEvent)
        if start_date is not None:
            stmt = stmt.where(AnalyticsEvent.created_at >= datetime.datetime.combine(start_date, datetime.time.min, tzinfo=datetime.timezone.utc))
        if end_date is not None:
            stmt = stmt.where(AnalyticsEvent.created_at <= datetime.datetime.combine(end_date, datetime.time.max, tzinfo=datetime.timezone.utc))

        if role or difficulty or college:
            stmt = stmt.join(Interview, Interview.id == AnalyticsEvent.interview_id, isouter=True)
            if role:
                stmt = stmt.where(Interview.track == role)
            if difficulty:
                stmt = stmt.where(Interview.difficulty == difficulty)
            if college:
                stmt = stmt.join(User, User.id == AnalyticsEvent.user_id, isouter=True).where(User.university == college)

        stmt = stmt.order_by(AnalyticsEvent.created_at.asc())
        return list((await self._db.execute(stmt)).scalars().all())


def _extract_overall_score(report: Report | None, summary_report: SummaryReport | None) -> float | None:
    if report and report.overall_score is not None:
        return _normalize_score(_to_float(report.overall_score))
    if summary_report and isinstance(summary_report.report_json, dict):
        score_summary = summary_report.report_json.get("overallScoreSummary") or summary_report.report_json.get("scoreSummary") or {}
        
        knowledge_comp = score_summary.get("knowledgeCompetence") or {}
        knowledge_pct = _to_float(knowledge_comp.get("averagePct") or knowledge_comp.get("percentage"))
        
        speech_struct = score_summary.get("speechStructure") or score_summary.get("speechAndStructure") or {}
        speech_pct = _to_float(speech_struct.get("averagePct") or speech_struct.get("percentage"))
        
        return _avg_non_null([knowledge_pct, speech_pct])
    return None


def _improvement_percent_from_interviews(
    interviews: list[Interview],
    reports: dict[int, Report],
    summary_reports: dict[int, SummaryReport] | None = None,
) -> float:
    summary_reports = summary_reports or {}
    by_user: dict[int, list[tuple[datetime.datetime, float]]] = defaultdict(list)
    for interview in interviews:
        score = _extract_overall_score(reports.get(interview.id), summary_reports.get(interview.id))
        if score is None:
            continue
        created_at = interview.created_at or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
        by_user[interview.user_id].append((created_at, score))

    improvements: list[float] = []
    for items in by_user.values():
        if len(items) < 2:
            continue
        items.sort(key=lambda x: x[0])
        first_score = items[0][1]
        latest_score = items[-1][1]
        improvements.append(latest_score - first_score)

    return _avg_non_null(improvements) if improvements else 0.0


def _extract_speech_score(report: Report | None, summary_report: SummaryReport | None) -> float | None:
    if report and isinstance(report.speech_structure_fluency, dict):
        section = report.speech_structure_fluency
        candidates = [
            section.get("average_communication_score"),
            section.get("averageCommunicationScore"),
            section.get("average_pace_score"),
            section.get("averagePauseScore"),
        ]
        score = _avg_non_null([_to_float(c) for c in candidates if c is not None])
        if score is not None:
            return _normalize_score(score)
    if summary_report and isinstance(summary_report.report_json, dict):
        return _normalize_score(
            _to_float(
                ((summary_report.report_json.get("scoreSummary") or {}).get("speechAndStructure") or {}).get("percentage")
            )
        )
    return None


def _extract_knowledge_score(report: Report | None, summary_report: SummaryReport | None) -> float | None:
    if report and isinstance(report.knowledge_competence, dict):
        section = report.knowledge_competence
        value = _to_float(section.get("average_domain_score") or section.get("averageDomainScore"))
        if value is not None:
            return _normalize_score(value)
    if summary_report and isinstance(summary_report.report_json, dict):
        return _normalize_score(
            _to_float(
                ((summary_report.report_json.get("scoreSummary") or {}).get("knowledgeCompetence") or {}).get("percentage")
            )
        )
    return None


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def _normalize_score(value: float | None) -> float | None:
    if value is None:
        return None
    if value <= 5:
        return value * 20
    return max(0.0, min(100.0, value))


def _avg_non_null(values: list[float | int | None]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _variance(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def _consistency_status(variance: float) -> str:
    if variance < 25:
        return "stable"
    if variance < 100:
        return "moderately_fluctuating"
    return "highly_fluctuating"


def _history_point(interview: Interview, attempt: QuestionAttempt, value: Any) -> dict[str, Any]:
    return {
        "interview_id": interview.id,
        "question_attempt_id": attempt.id,
        "timestamp": attempt.created_at,
        "value": value,
    }


def _compute_weak_area_tags(metric_history: dict[str, list[dict[str, Any]]]) -> list[str]:
    tags: list[str] = []

    filler_vals = [entry["value"] for entry in metric_history.get("filler_density", []) if isinstance(entry.get("value"), (int, float))]
    if filler_vals and _avg_non_null(filler_vals) is not None and _avg_non_null(filler_vals) > 0.08:
        tags.append("high_filler_usage")

    energy_vals = [entry["value"] for entry in metric_history.get("energy", []) if isinstance(entry.get("value"), (int, float))]
    if energy_vals and _avg_non_null(energy_vals) is not None and _avg_non_null(energy_vals) < 45:
        tags.append("low_energy")

    structure_vals = [entry["value"] for entry in metric_history.get("structure_quality", []) if isinstance(entry.get("value"), (int, float))]
    if structure_vals and _avg_non_null(structure_vals) is not None and _avg_non_null(structure_vals) < 50:
        tags.append("poor_structure")

    relevance_vals = [entry["value"] for entry in metric_history.get("relevance", []) if isinstance(entry.get("value"), (int, float))]
    if relevance_vals and _avg_non_null(relevance_vals) is not None and _avg_non_null(relevance_vals) < 45:
        tags.append("too_short_answers")

    return tags


def _find_interview(interviews: list[Interview], interview_id: int) -> Interview | None:
    for interview in interviews:
        if interview.id == interview_id:
            return interview
    return None


def _average_gap_hours(times: list[datetime.datetime]) -> float | None:
    if len(times) < 2:
        return None
    ordered = sorted(times)
    gaps = []
    for idx in range(1, len(ordered)):
        gaps.append((ordered[idx] - ordered[idx - 1]).total_seconds() / 3600)
    return sum(gaps) / len(gaps) if gaps else None


def _question_type(category: str | None) -> str:
    category_norm = (category or "tech").lower()
    if category_norm == "self":
        return "Self"
    if category_norm == "behavioral":
        return "Behavioral"
    if category_norm == "productivity":
        return "Productivity"
    if category_norm in {"company_candidate", "company-candidate", "company_candidate_qna"}:
        return "Company/Candidate"
    if category_norm == "general":
        return "General"
    if category_norm in {"tech_allied", "tech-allied", "techallied"}:
        return "Tech-allied"
    return "Technical"


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def _unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            output.append(item)
    return output


def _dropoff(prev_count: int, next_count: int) -> float:
    if prev_count <= 0:
        return 0.0
    return round((1 - (next_count / prev_count)) * 100.0, 2)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if not xs or not ys:
        return None
    n = min(len(xs), len(ys))
    x = xs[:n]
    y = ys[:n]
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    den_x = math.sqrt(sum((a - mean_x) ** 2 for a in x))
    den_y = math.sqrt(sum((b - mean_y) ** 2 for b in y))
    if den_x == 0 or den_y == 0:
        return None
    return numerator / (den_x * den_y)


def _histogram(values: list[float], bins: list[int]) -> list[dict[str, Any]]:
    if not values:
        return []
    output: list[dict[str, Any]] = []
    for idx in range(len(bins) - 1):
        low = bins[idx]
        high = bins[idx + 1]
        count = len([v for v in values if low <= v < high or (idx == len(bins) - 2 and low <= v <= high)])
        output.append({"range": f"{low}-{high}", "count": count})
    return output


def _is_distribution_too_narrow(values: list[float]) -> bool:
    if len(values) < 10:
        return False
    std_dev = math.sqrt(_variance(values))
    return std_dev < 7.5
