"""Bounded derived coaching fields; never serialize source report objects."""

import math


def _feedback_summary(report):
    if not isinstance(report, dict):
        return None
    for key in ("executiveSummary", "summary", "overallSummary"):
        value = report.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    overall = report.get("overallFeedback")
    if isinstance(overall, dict):
        for value in overall.values():
            if isinstance(value, dict):
                value = value.get("summary") or value.get("feedback")
            if isinstance(value, str) and value.strip():
                return value.strip()[:500]
    return None


def review_fields(report):
    score = None
    if isinstance(report, dict):
        summary = report.get("overallScoreSummary") or report.get("scoreSummary")
        if isinstance(summary, dict):
            for key in ("overallPercentage", "overallScore", "percentage"):
                value = summary.get(key)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and 0 <= value <= 100
                    and math.isfinite(value)
                ):
                    score = round(float(value), 2)
                    break
    feedback = _feedback_summary(report)
    return {
        "state": "available" if score is not None or feedback else "unavailable",
        "score": score,
        "feedback": feedback,
    }
