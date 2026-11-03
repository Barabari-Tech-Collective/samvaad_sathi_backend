import hashlib
import json
import runpy
from pathlib import Path
import pytest
from src.services.jev_rollout import validate_label_review

MODULE = runpy.run_path(
    str(Path(__file__).resolve().parents[4] / "ops/review_jev_cases.py")
)


def source(tmp_path):
    p = tmp_path / "predictions.json"
    p.write_text(
        json.dumps(
            {
                "datasetId": "test",
                "model": "test",
                "task": "bulk_ats_semantics_v1",
                "sourceApproved": True,
                "deidentified": True,
                "cases": [
                    {
                        "id": str(i),
                        "segment": "technical",
                        "context": f"Deidentified test context {i}",
                        "predictedReview": bool(i % 2),
                        "predictedScore": 75,
                    }
                    for i in range(10)
                ],
            }
        )
    )
    return p


def fill(path, reviewer):
    data = json.loads(path.read_text())
    data["reviewer_id"] = reviewer
    for row in data["labels"]:
        row["expectedReview"] = bool(int(row["id"]) % 2)
        row["expectedScore"] = 75
    path.write_text(json.dumps(data))


def test_blind_packets_bind_labels_and_keep_predictions_hidden(tmp_path):
    p = source(tmp_path)
    folder = tmp_path / "review"
    MODULE["prepare"](p, folder)
    assert "predictedScore" not in (folder / "review-a.json").read_text()
    fill(folder / "review-a.json", "reviewer-a")
    fill(folder / "review-b.json", "reviewer-b")
    output = tmp_path / "ready"
    assert MODULE["assemble"](
        p, folder / "review-a.json", folder / "review-b.json", output
    )["ready"]
    manifest = json.loads((output / "review-manifest.json").read_text())
    assert (
        manifest["cases_sha256"]
        == hashlib.sha256((output / "paired-cases.json").read_bytes()).hexdigest()
    )
    validate_label_review(
        manifest,
        dataset_id="test",
        model="test",
        task="bulk_ats_semantics_v1",
        case_count=10,
        case_digest=manifest["cases_sha256"],
    )


def test_same_reviewer_and_changed_context_cannot_supply_gold_labels(tmp_path):
    p = source(tmp_path)
    folder = tmp_path / "review"
    MODULE["prepare"](p, folder)
    fill(folder / "review-a.json", "reviewer-a")
    fill(folder / "review-b.json", "reviewer-a")
    with pytest.raises(ValueError):
        MODULE["assemble"](
            p, folder / "review-a.json", folder / "review-b.json", tmp_path / "bad"
        )
    fill(folder / "review-b.json", "reviewer-b")
    data = json.loads((folder / "review-b.json").read_text())
    data["labels"][0]["context"] = "Different evidence"
    (folder / "review-b.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        MODULE["assemble"](
            p, folder / "review-a.json", folder / "review-b.json", tmp_path / "bad2"
        )


def test_disputes_require_third_reviewer_and_written_reason(tmp_path):
    p = source(tmp_path)
    folder = tmp_path / "review"
    MODULE["prepare"](p, folder)
    fill(folder / "review-a.json", "reviewer-a")
    fill(folder / "review-b.json", "reviewer-b")
    data = json.loads((folder / "review-b.json").read_text())
    data["labels"][0]["expectedScore"] = 50
    (folder / "review-b.json").write_text(json.dumps(data))
    disputes = tmp_path / "disputes"
    assert not MODULE["assemble"](
        p, folder / "review-a.json", folder / "review-b.json", disputes
    )["ready"]
    decision = disputes / "adjudication-required.json"
    fill(decision, "reviewer-a")
    data = json.loads(decision.read_text())
    data["labels"][0]["reason"] = "Independent evidence review completed."
    decision.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        MODULE["assemble"](
            p,
            folder / "review-a.json",
            folder / "review-b.json",
            tmp_path / "bad",
            decision,
        )
    fill(decision, "reviewer-c")
    data = json.loads(decision.read_text())
    data["labels"][0]["reason"] = "Independent evidence review completed."
    decision.write_text(json.dumps(data))
    assert MODULE["assemble"](
        p,
        folder / "review-a.json",
        folder / "review-b.json",
        tmp_path / "resolved",
        decision,
    )["ready"]


def test_small_review_samples_report_uncertainty():
    from src.services.jev_rollout import proportion_interval

    low, high = proportion_interval(10, 10)
    assert 0.7 < low < 0.8 and high > 0.99
    assert proportion_interval(0, 0) is None
