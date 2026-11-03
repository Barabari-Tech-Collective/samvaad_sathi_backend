"""Who looks ready to be put forward (a transparent rule check, never a hidden score).

Each learner is checked against the course's readiness rules and every check is returned with
the number behind it, so a person can see why and decides for themselves."""

import datetime as dt

import sqlalchemy as sa

from src.models.db.assessment_assignment import AssessmentAssignment
from src.models.db.interview import Interview
from src.models.db.practice_drill_completion import PracticeDrillCompletion
from src.models.db.readiness_rule import ReadinessRule
from src.models.db.summary_report import SummaryReport

UTC = dt.timezone.utc
DEFAULTS = {
    "window_days": 60, "min_mock_interviews": 3, "min_average_score": 60,
    "min_drills": 5, "require_improving": False,
}


def score_of(report_json):
    if not isinstance(report_json, dict):
        return None
    summary = report_json.get("overallScoreSummary") or report_json.get("scoreSummary") or {}
    for key in ("overallPercentage", "overallScore", "percentage"):
        value = summary.get(key) if isinstance(summary, dict) else None
        if isinstance(value, (int, float)):
            return float(value)
    return None


async def rule_for(session, course_id):
    row = (
        await session.execute(sa.select(ReadinessRule).where(ReadinessRule.course_id == course_id))
    ).scalar_one_or_none()
    return row


def rule_view(row):
    if row is None:
        return DEFAULTS | {"version": 0, "is_default": True}
    return {
        "window_days": row.window_days, "min_mock_interviews": row.min_mock_interviews,
        "min_average_score": row.min_average_score, "min_drills": row.min_drills,
        "require_improving": row.require_improving, "version": row.version, "is_default": False,
    }


def judge(rule, scores, mocks, drills):
    """scores: scored mock percentages oldest first. Returns (status, checks)."""
    average = round(sum(scores) / len(scores), 1) if scores else None
    improving = len(scores) >= 2 and scores[-1] >= scores[0]
    checks = [
        {"key": "mocks", "label": f"At least {rule['min_mock_interviews']} finished mock interviews",
         "value": mocks, "passed": mocks >= rule["min_mock_interviews"]},
        {"key": "score", "label": f"Average score at least {rule['min_average_score']}%",
         "value": average, "passed": average is not None and average >= rule["min_average_score"]},
        {"key": "drills", "label": f"At least {rule['min_drills']} finished drills",
         "value": drills, "passed": drills >= rule["min_drills"]},
    ]
    if rule["require_improving"]:
        checks.append(
            {"key": "improving", "label": "Latest score is not lower than the first",
             "value": scores[-1] - scores[0] if len(scores) >= 2 else None, "passed": improving}
        )
    failed = [c for c in checks if not c["passed"]]
    status = "ready" if not failed else "almost" if len(failed) == 1 else "not_yet"
    return status, checks


async def assess(session, course_id, student_ids, rule, now=None):
    """Readiness for each student id, keyed by id."""
    now = now or dt.datetime.now(UTC)
    since = now - dt.timedelta(days=rule["window_days"])
    scores = {sid: [] for sid in student_ids}
    mocks = {sid: 0 for sid in student_ids}
    if student_ids:
        rows = (
            await session.execute(
                sa.select(Interview.user_id, SummaryReport.report_json)
                .outerjoin(SummaryReport, SummaryReport.interview_id == Interview.id)
                .where(
                    Interview.user_id.in_(student_ids),
                    Interview.course_id == course_id,
                    Interview.interview_mode == "practice",
                    Interview.status == "completed",
                    Interview.completed_at >= since,
                )
                .order_by(Interview.completed_at)
            )
        ).all()
        for uid, report in rows:
            mocks[uid] += 1
            value = score_of(report)
            if value is not None:
                scores[uid].append(value)
        drills = {
            uid: int(n)
            for uid, n in (
                await session.execute(
                    sa.select(PracticeDrillCompletion.user_id, sa.func.count(PracticeDrillCompletion.id))
                    .where(
                        PracticeDrillCompletion.user_id.in_(student_ids),
                        PracticeDrillCompletion.completed_at >= since,
                    )
                    .group_by(PracticeDrillCompletion.user_id)
                )
            ).all()
        }
        put_forward = set(
            (
                await session.execute(
                    sa.select(AssessmentAssignment.student_id).where(
                        AssessmentAssignment.student_id.in_(student_ids),
                        AssessmentAssignment.course_id == course_id,
                        AssessmentAssignment.status.in_(("assigned", "started")),
                    )
                )
            ).scalars()
        )
    else:
        drills, put_forward = {}, set()
    result = {}
    for sid in student_ids:
        status, checks = judge(rule, scores[sid], mocks[sid], drills.get(sid, 0))
        result[sid] = {"status": status, "checks": checks, "already_assigned": sid in put_forward}
    return result
