#!/usr/bin/env python3
"""Evaluate Bulk ATS ranking calibration and group-level error rates.

CSV columns: candidate_id, ats_score, human_score, selected and optionally
protected_group. Protected-group data is used only for offline auditing and
must never be sent into the scoring pipeline.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict


def _truth(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "selected", "hired"}


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denominator = math.sqrt(
        sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)
    )
    return round(numerator / denominator, 4) if denominator else None


def _ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    result = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index
        while end + 1 < len(indexed) and indexed[end + 1][1] == indexed[index][1]:
            end += 1
        rank = (index + end + 2) / 2
        for position in range(index, end + 1):
            result[indexed[position][0]] = rank
        index = end + 1
    return result


def evaluate(rows: list[dict]) -> dict:
    scored = sorted(rows, key=lambda row: float(row["ats_score"]), reverse=True)
    positives = sum(_truth(row["selected"]) for row in scored)
    k = max(1, positives)
    predicted_ids = {row["candidate_id"] for row in scored[:k]}
    true_positives = sum(
        _truth(row["selected"]) and row["candidate_id"] in predicted_ids
        for row in scored
    )
    ats = [float(row["ats_score"]) for row in rows]
    human = [float(row["human_score"]) for row in rows]
    group_rows: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("protected_group", "").strip():
            group_rows[row["protected_group"].strip()].append(row)

    groups = {}
    rates = []
    for group, members in sorted(group_rows.items()):
        actual_positive = sum(_truth(row["selected"]) for row in members)
        predicted_positive = sum(
            row["candidate_id"] in predicted_ids for row in members
        )
        false_negative = sum(
            _truth(row["selected"]) and row["candidate_id"] not in predicted_ids
            for row in members
        )
        selection_rate = predicted_positive / len(members) if members else 0
        rates.append(selection_rate)
        groups[group] = {
            "count": len(members),
            "selectionRate": round(selection_rate, 4),
            "falseNegativeRate": (
                round(false_negative / actual_positive, 4) if actual_positive else None
            ),
        }

    return {
        "sampleSize": len(rows),
        "warning": "Use at least 300-500 representative, independently labelled resumes before treating results as validated.",
        "meanAbsoluteError": round(
            statistics.mean(abs(a - h) for a, h in zip(ats, human)), 4
        ),
        "pearsonCorrelation": _pearson(ats, human),
        "spearmanCorrelation": _pearson(_ranks(ats), _ranks(human)),
        "precisionAtK": round(true_positives / k, 4),
        "recallAtK": round(true_positives / positives, 4) if positives else None,
        "k": k,
        "groups": groups,
        "fourFifthsRatio": (
            round(min(rates) / max(rates), 4) if rates and max(rates) else None
        ),
        "policy": "Audit outputs identify review risks; they must not be used to infer or score protected characteristics.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--output")
    args = parser.parse_args()
    with open(args.csv_path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    required = {"candidate_id", "ats_score", "human_score", "selected"}
    if not rows or not required.issubset(rows[0]):
        raise SystemExit(f"CSV must contain: {', '.join(sorted(required))}")
    report = json.dumps(evaluate(rows), indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(report + "\n")
    else:
        print(report)


if __name__ == "__main__":
    main()
