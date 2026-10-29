"""Offline evaluation and deterministic, task-specific Jev release gates.

No network calls, student text or credentials belong in an evaluation artifact.
Thresholds are conservative initial release criteria, not a claim of calibration.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

TASK = "bulk_ats_semantics_v1"
POLICY_VERSION = "jev-ats-release-v2"
TASKS = {
    TASK: POLICY_VERSION,
    "practice_rubric_feedback_v1": "jev-practice-rubric-release-v2",
}


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def proportion_interval(successes, count):
    """Wilson 95% interval; small samples must not look certain."""
    if not count:
        return None
    z = 1.959963984540054
    p = successes / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    radius = (
        z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    )
    return [max(0.0, center - radius), min(1.0, center + radius)]


def evaluate_cases(
    cases: list[dict],
    *,
    model: str,
    dataset_id: str,
    now=None,
    task: str = TASK,
    label_review: dict | None = None,
):
    """Paired human labels and provider predictions; no model self-grading.

    Each case has a unique opaque id, a predeclared segment, expected/predicted
    review booleans and expected/predicted semantic score percentages. A failed
    provider prediction is represented by null, never silently dropped.
    """
    if task not in TASKS:
        raise ValueError("Unsupported evaluation task")
    if not model.strip() or not dataset_id.strip() or not cases:
        raise ValueError("Model, dataset id and labelled cases are required")
    if len(cases) > 10000:
        raise ValueError("Evaluation is limited to 10000 cases")
    seen = set()
    groups = {}
    for case in cases:
        if (
            not {
                "id",
                "segment",
                "expectedReview",
                "predictedReview",
                "expectedScore",
                "predictedScore",
            }
            <= case.keys()
        ):
            raise ValueError("Every paired case must include all required fields")
        case_id, segment = case.get("id"), case.get("segment")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise ValueError("Cases require unique nonempty opaque ids")
        if not isinstance(segment, str) or not segment.strip():
            raise ValueError("Every case requires a predeclared segment")
        seen.add(case_id)
        for key in ("expectedReview", "predictedReview"):
            if type(case.get(key)) is not bool:
                raise ValueError(f"{key} must be a boolean")
        expected, predicted = case.get("expectedScore"), case.get("predictedScore")
        if expected is None:
            if not case["expectedReview"]:
                raise ValueError("Unscorable cases must require human review")
        elif not finite_number(expected) or not 0 <= expected <= 100:
            raise ValueError("Expected scores must be finite percentages or unscorable")
        if predicted is not None and (
            not finite_number(predicted) or not 0 <= predicted <= 100
        ):
            raise ValueError("Predicted scores must be null or finite percentages")
        groups.setdefault(segment, []).append(case)

    def metrics(rows):
        positives = sum(row["expectedReview"] for row in rows)
        negatives = len(rows) - positives
        scored_rows = [row for row in rows if row["expectedScore"] is not None]
        errors = [
            abs(row["predictedScore"] - row["expectedScore"])
            for row in scored_rows
            if row["predictedScore"] is not None
        ]
        missed = sum(
            row["expectedReview"] and not row["predictedReview"] for row in rows
        )
        false_alarms = sum(
            not row["expectedReview"] and row["predictedReview"] for row in rows
        )
        return {
            "count": len(rows),
            "reviewPositiveCount": positives,
            "reviewNegativeCount": negatives,
            "scoreLabelCount": len(scored_rows),
            "reviewRecall": 1 - missed / positives if positives else None,
            "reviewRecall95Interval": proportion_interval(
                positives - missed, positives
            ),
            "falseReviewRate95Interval": proportion_interval(false_alarms, negatives),
            "falseReviewRate": false_alarms / negatives if negatives else None,
            "scoreMAE": sum(errors) / len(errors) if errors else None,
            "predictionCoverage": len(errors) / len(scored_rows) if scored_rows else 0,
        }

    overall = metrics(cases)
    segments = {name: metrics(rows) for name, rows in sorted(groups.items())}
    failures = []
    for name, result in [("overall", overall), *segments.items()]:
        if result["count"] < (50 if name == "overall" else 20):
            failures.append(f"{name}:insufficient_cases")
        if result["reviewPositiveCount"] < 10 or result["reviewNegativeCount"] < 10:
            failures.append(f"{name}:insufficient_review_labels")
        if result["scoreLabelCount"] < (20 if name == "overall" else 10):
            failures.append(f"{name}:insufficient_score_labels")
        for key, threshold, direction in [
            ("reviewRecall", 0.95, "min"),
            ("falseReviewRate", 0.20, "max"),
            ("scoreMAE", 12.5, "max"),
            ("predictionCoverage", 0.98, "min"),
        ]:
            value = result[key]
            if value is None or (
                value < threshold if direction == "min" else value > threshold
            ):
                failures.append(f"{name}:{key}")
    instant = now or datetime.now(timezone.utc)
    return {
        "labelReview": label_review,
        "policyVersion": TASKS[task],
        "task": task,
        "model": model,
        "datasetId": dataset_id,
        "evaluatedAt": instant.isoformat(),
        "expiresAt": (instant + timedelta(days=30)).isoformat(),
        "overall": overall,
        "segments": segments,
        "passed": not failures,
        "failures": failures,
    }


def validate_label_review(
    review, *, dataset_id, model, task, case_count, case_digest=None, now=None
):
    if not isinstance(review, dict):
        raise ValueError("Independent label review is required")
    reviewers = review.get("reviewer_ids")
    if (
        not isinstance(reviewers, list)
        or len(reviewers) != 2
        or any(
            not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", value)
            for value in reviewers
        )
        or len(set(reviewers)) != 2
    ):
        raise ValueError("Two independent reviewer identities are required")
    if any(
        review.get(key) != value
        for key, value in [
            ("datasetId", dataset_id),
            ("model", model),
            ("task", task),
            ("case_count", case_count),
        ]
    ):
        raise ValueError("Review does not match evaluation task/model/dataset")
    digest = review.get("cases_sha256", "")
    if (
        not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or (case_digest is not None and digest != case_digest)
    ):
        raise ValueError("Reviewed cases digest does not match")
    disagreements = review.get("disagreement_count")
    if (
        type(disagreements) is not int
        or not 0 <= disagreements <= case_count
        or case_count < 1
    ):
        raise ValueError("Invalid review counts")
    rate = review.get("agreement_rate")
    if not finite_number(rate) or not math.isclose(
        rate, 1 - disagreements / case_count
    ):
        raise ValueError("Invalid inter-reviewer agreement")
    third = review.get("adjudicator_id")
    if disagreements and (
        not isinstance(third, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", third)
        or third in reviewers
    ):
        raise ValueError("Disagreements require independent adjudication")
    reviewed = datetime.fromisoformat(review["reviewedAt"])
    if not reviewed.tzinfo or reviewed > (now or datetime.now(timezone.utc)):
        raise ValueError("Review time must be aware and cannot be future-dated")
    return review


def rollout_allowed(
    *,
    path: str,
    digest: str,
    model: str,
    percent: int,
    cohort_key: str,
    segment: str | None = None,
    now=None,
    task: str = TASK,
) -> bool:
    """Operator-pinned evidence + stable cohort. Any ambiguity keeps Jev off."""
    if task not in TASKS:
        return False
    if (
        type(percent) is not int
        or not 0 < percent <= 100
        or not cohort_key
        or not model
    ):
        return False
    if not path or len(digest) != 64:
        return False
    try:
        with Path(path).open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != digest.lower():
            return False
        report = json.loads(raw)
        instant = now or datetime.now(timezone.utc)
        evaluated = datetime.fromisoformat(report["evaluatedAt"])
        expires = datetime.fromisoformat(report["expiresAt"])
        if (
            report["task"] != task
            or report["policyVersion"] != TASKS[task]
            or report["model"] != model
            or report["passed"] is not True
            or report["failures"] != []
            or not report["datasetId"]
            or not evaluated.tzinfo
            or not expires.tzinfo
            or not evaluated <= instant < expires
            or expires - evaluated > timedelta(days=30)
        ):
            return False
        validate_label_review(
            report.get("labelReview"),
            dataset_id=report["datasetId"],
            model=model,
            task=task,
            case_count=report["overall"]["count"],
            now=evaluated,
        )
        # Recheck aggregate thresholds even for a digest-pinned operator artifact.
        results = [report["overall"], *report["segments"].values()]
        if not report["segments"]:
            return False
        if segment is not None and segment not in report["segments"]:
            return False
        for index, result in enumerate(results):
            for field in (
                "count",
                "reviewPositiveCount",
                "reviewNegativeCount",
                "scoreLabelCount",
                "reviewRecall",
                "falseReviewRate",
                "scoreMAE",
                "predictionCoverage",
            ):
                if not finite_number(result[field]):
                    return False
            if (
                result["count"] < (50 if index == 0 else 20)
                or result["reviewPositiveCount"] < 10
                or result["reviewNegativeCount"] < 10
                or not (20 if index == 0 else 10)
                <= result["scoreLabelCount"]
                <= result["count"]
                or not 0.95 <= result["reviewRecall"] <= 1
                or not 0 <= result["falseReviewRate"] <= 0.20
                or not 0 <= result["scoreMAE"] <= 12.5
                or not 0.98 <= result["predictionCoverage"] <= 1
            ):
                return False
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False
    bucket = (
        int.from_bytes(
            hashlib.sha256(f"{task}:{cohort_key}".encode()).digest()[:8], "big"
        )
        % 100
    )
    return bucket < percent
