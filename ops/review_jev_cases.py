"""Prepare blind staff review packets and assemble independently reviewed labels.

No provider calls. Input must be an approved deidentified prediction dataset:
{datasetId, model, task, deidentified:true, sourceApproved:true, cases:[
{id,segment,context,predictedReview,predictedScore}]}. Staff fill expectedReview
and expectedScore in the packets. Differing labels require a third reviewer.
"""

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path


def read(path):
    raw = Path(path).read_bytes()
    if len(raw) > 4_000_000:
        raise ValueError("Review input exceeds 4MB")
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def dataset(path):
    data, digest = read(path)
    if data.get("deidentified") is not True or data.get("sourceApproved") is not True:
        raise ValueError("Deidentification and source approval must be recorded")
    if not all(
        isinstance(data.get(key), str) and data[key].strip()
        for key in ("datasetId", "model", "task")
    ):
        raise ValueError("Dataset, model and task are required")
    rows = data.get("cases")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
        raise ValueError("Expected 1–10000 prediction cases")
    ids = [row.get("id") for row in rows]
    if any(not isinstance(i, str) or not i.strip() for i in ids) or len(
        set(ids)
    ) != len(ids):
        raise ValueError("Cases require unique opaque IDs")
    for row in rows:
        if (
            not isinstance(row.get("segment"), str)
            or not row["segment"]
            or not isinstance(row.get("context"), str)
            or not row["context"].strip()
        ):
            raise ValueError(
                "Each case needs a segment and deidentified review context"
            )
        if len(row["context"]) > 16000 or type(row.get("predictedReview")) is not bool:
            raise ValueError("Invalid prediction or review context")
        score = row.get("predictedScore")
        if score is not None and (
            type(score) not in (int, float) or not 0 <= score <= 100
        ):
            raise ValueError("Invalid predicted score")
    return data, digest


def packet(data, digest, rows=None):
    return {
        "packet_sha256": digest,
        "datasetId": data["datasetId"],
        "task": data["task"],
        "reviewer_id": "",
        "labels": [
            {
                "id": row["id"],
                "segment": row["segment"],
                "context": row["context"],
                "expectedReview": None,
                "expectedScore": None,
            }
            for row in (rows or data["cases"])
        ],
    }


def labels(path, digest, source, *, adjudication=False):
    data, _ = read(path)
    reviewer = data.get("reviewer_id")
    if (
        not isinstance(reviewer, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", reviewer)
        or data.get("packet_sha256") != digest
    ):
        raise ValueError("Review identity or packet binding is invalid")
    rows = data.get("labels")
    if not isinstance(rows, list) or len(rows) != len(source):
        raise ValueError("Review must label every assigned case exactly once")
    result = {}
    for row in rows:
        key = row.get("id")
        if (
            key not in source
            or key in result
            or row.get("context") != source[key]["context"]
            or row.get("segment") != source[key]["segment"]
        ):
            raise ValueError("Review case/context was changed, duplicated or omitted")
        review, score = row.get("expectedReview"), row.get("expectedScore")
        if (
            type(review) is not bool
            or (score is None and not review)
            or (
                score is not None
                and (type(score) not in (int, float) or not 0 <= score <= 100)
            )
        ):
            raise ValueError("Invalid gold label; unscorable cases require review")
        if adjudication and (
            not isinstance(row.get("reason"), str) or len(row["reason"].strip()) < 10
        ):
            raise ValueError("Disputed labels require a written adjudication reason")
        result[key] = (review, score)
    return reviewer, result


def write(path, data):
    with path.open("x") as output:
        json.dump(data, output, indent=2, allow_nan=False)
        output.write("\n")


def prepare(source, output):
    data, digest = dataset(source)
    output.mkdir(parents=True, exist_ok=False)
    for name in ["review-a.json", "review-b.json"]:
        write(output / name, packet(data, digest))
    return {"cases": len(data["cases"]), "packet_sha256": digest}


def assemble(source, review_a, review_b, output, adjudication=None):
    data, digest = dataset(source)
    source_rows = {row["id"]: row for row in data["cases"]}
    a, left = labels(review_a, digest, source_rows)
    b, right = labels(review_b, digest, source_rows)
    if a == b:
        raise ValueError("Independent reviewers must have different identities")
    disputes = {key: source_rows[key] for key in source_rows if left[key] != right[key]}
    third = None
    resolved = {}
    output.mkdir(parents=True, exist_ok=False)
    if disputes and not adjudication:
        write(
            output / "adjudication-required.json",
            packet(data, digest, list(disputes.values())),
        )
        return {"ready": False, "disagreements": len(disputes)}
    if disputes:
        third, resolved = labels(adjudication, digest, disputes, adjudication=True)
        if third in {a, b}:
            raise ValueError("Disputes require a third independent reviewer")
    paired = []
    for key, row in source_rows.items():
        review, score = resolved.get(key, left[key])
        paired.append(
            {
                "id": key,
                "segment": row["segment"],
                "expectedReview": review,
                "expectedScore": score,
                "predictedReview": row["predictedReview"],
                "predictedScore": row.get("predictedScore"),
            }
        )
    write(output / "paired-cases.json", paired)
    _, paired_digest = read(output / "paired-cases.json")
    manifest = {
        "datasetId": data["datasetId"],
        "model": data["model"],
        "task": data["task"],
        "cases_sha256": paired_digest,
        "case_count": len(paired),
        "reviewer_ids": [a, b],
        "adjudicator_id": third,
        "disagreement_count": len(disputes),
        "agreement_rate": 1 - len(disputes) / len(paired),
        "reviewedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    write(output / "review-manifest.json", manifest)
    return {"ready": True, "cases": len(paired), "disagreements": len(disputes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("predictions", type=Path)
    prepare_parser.add_argument("--output-dir", required=True, type=Path)
    assembly = sub.add_parser("assemble")
    assembly.add_argument("predictions", type=Path)
    assembly.add_argument("--review-a", required=True, type=Path)
    assembly.add_argument("--review-b", required=True, type=Path)
    assembly.add_argument("--adjudication", type=Path)
    assembly.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = (
            prepare(args.predictions, args.output_dir)
            if args.action == "prepare"
            else assemble(
                args.predictions,
                args.review_a,
                args.review_b,
                args.output_dir,
                args.adjudication,
            )
        )
        print(json.dumps(result))
        return 0 if result.get("ready", True) else 1
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
        print(f"Review packet rejected: {type(error).__name__}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
