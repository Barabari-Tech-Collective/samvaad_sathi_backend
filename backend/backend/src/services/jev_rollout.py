"""Offline evaluation and deterministic, task-specific Jev release gates.

No network calls, student text or credentials belong in an evaluation artifact.
Thresholds are conservative initial release criteria, not a claim of calibration.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

TASK = "bulk_ats_semantics_v1"
POLICY_VERSION = "jev-ats-release-v1"


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def evaluate_cases(cases: list[dict], *, model: str, dataset_id: str, now=None):
    """Paired human labels and provider predictions; no model self-grading.

    Each case has a unique opaque id, a predeclared segment, expected/predicted
    review booleans and expected/predicted semantic score percentages. A failed
    provider prediction is represented by null, never silently dropped.
    """
    if not model.strip() or not dataset_id.strip() or not cases:
        raise ValueError("Model, dataset id and labelled cases are required")
    if len(cases) > 10000:
        raise ValueError("Evaluation is limited to 10000 cases")
    seen = set()
    groups = {}
    for case in cases:
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
        if not finite_number(expected) or not 0 <= expected <= 100:
            raise ValueError("Expected scores must be finite percentages")
        if predicted is not None and (
            not finite_number(predicted) or not 0 <= predicted <= 100
        ):
            raise ValueError("Predicted scores must be null or finite percentages")
        groups.setdefault(segment, []).append(case)

    def metrics(rows):
        positives = sum(row["expectedReview"] for row in rows)
        negatives = len(rows) - positives
        errors = [
            abs(row["predictedScore"] - row["expectedScore"])
            for row in rows
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
            "reviewRecall": 1 - missed / positives if positives else None,
            "falseReviewRate": false_alarms / negatives if negatives else None,
            "scoreMAE": sum(errors) / len(errors) if errors else None,
            "predictionCoverage": len(errors) / len(rows),
        }

    overall = metrics(cases)
    segments = {name: metrics(rows) for name, rows in sorted(groups.items())}
    failures = []
    for name, result in [("overall", overall), *segments.items()]:
        if result["count"] < (50 if name == "overall" else 20):
            failures.append(f"{name}:insufficient_cases")
        if result["reviewPositiveCount"] < 10 or result["reviewNegativeCount"] < 10:
            failures.append(f"{name}:insufficient_review_labels")
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
        "policyVersion": POLICY_VERSION,
        "task": TASK,
        "model": model,
        "datasetId": dataset_id,
        "evaluatedAt": instant.isoformat(),
        "expiresAt": (instant + timedelta(days=30)).isoformat(),
        "overall": overall,
        "segments": segments,
        "passed": not failures,
        "failures": failures,
    }


def rollout_allowed(
    *,
    path: str,
    digest: str,
    model: str,
    percent: int,
    cohort_key: str,
    segment: str | None = None,
    now=None,
) -> bool:
    """Operator-pinned evidence + stable cohort. Any ambiguity keeps Jev off."""
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
            report["task"] != TASK
            or report["policyVersion"] != POLICY_VERSION
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
            hashlib.sha256(f"{TASK}:{cohort_key}".encode()).digest()[:8], "big"
        )
        % 100
    )
    return bucket < percent
