"""Evaluate paired, deidentified ATS semantic predictions without provider calls.

Usage: python ops/evaluate_jev.py cases.json --model PINNED_MODEL
       --dataset-id REVIEWED_DATASET --output evaluation.json
Exit 0: initial release gates pass; 1: gates fail; 2: invalid input.
Input format: a JSON array matching services.jev_rollout.evaluate_cases.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend/backend"))
from src.services.jev_rollout import evaluate_cases, TASKS, TASK, validate_label_review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--task", choices=sorted(TASKS), default=TASK)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-manifest", type=Path)
    args = parser.parse_args()
    try:
        with args.cases.open("rb") as source:
            raw = source.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("Input exceeds 4MB")
        cases = json.loads(raw)
        if not isinstance(cases, list) or not all(
            isinstance(row, dict) for row in cases
        ):
            raise ValueError("Input must be an array of paired case objects")
        label_review = None
        if args.review_manifest:
            with args.review_manifest.open("rb") as source:
                review_raw = source.read(65537)
            if len(review_raw) > 65536:
                raise ValueError("Review manifest exceeds 64KiB")
            label_review = validate_label_review(
                json.loads(review_raw),
                dataset_id=args.dataset_id,
                model=args.model,
                task=args.task,
                case_count=len(cases),
                case_digest=hashlib.sha256(raw).hexdigest(),
            )
        report = evaluate_cases(
            cases, model=args.model, dataset_id=args.dataset_id, task=args.task
        )
        report["labelReview"] = label_review
        report["eligibleForRollout"] = report["passed"] and label_review is not None
        output = (json.dumps(report, indent=2, allow_nan=False) + "\n").encode()
        if len(output) > 65536:
            raise ValueError("Report exceeds the runtime artifact limit")
        # Never replace a previously reviewed evidence file accidentally.
        with args.output.open("xb") as target:
            target.write(output)
        print(
            json.dumps(
                {
                    "passed": report["passed"],
                    "eligibleForRollout": report["eligibleForRollout"],
                    "failures": report["failures"],
                    "sha256": hashlib.sha256(output).hexdigest(),
                }
            )
        )
        return 0 if report["eligibleForRollout"] else 1
    except (OSError, ValueError, TypeError) as error:
        print(f"Evaluation rejected: {type(error).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
