import hashlib
import json
import runpy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services.jev_rollout import evaluate_cases, rollout_allowed
from src.services.ai_resume.bulk import jev_client

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def cases():
    return [
        dict(
            id=str(i),
            segment="communication" if i < 30 else "technical",
            expectedReview=i % 2 == 0,
            predictedReview=i % 2 == 0,
            expectedScore=75,
            predictedScore=75,
        )
        for i in range(60)
    ]


def label_review(
    model="pinned-test-model",
    dataset_id="synthetic-unit-only",
    task="bulk_ats_semantics_v1",
):
    return {
        "datasetId": dataset_id,
        "model": model,
        "task": task,
        "cases_sha256": "0" * 64,
        "case_count": 60,
        "reviewer_ids": ["synthetic-a", "synthetic-b"],
        "adjudicator_id": None,
        "disagreement_count": 0,
        "agreement_rate": 1.0,
        "reviewedAt": NOW.isoformat(),
    }


def artifact(tmp_path, **changes):
    report = evaluate_cases(
        cases(),
        model="pinned-test-model",
        dataset_id="synthetic-unit-only",
        now=NOW,
        label_review=label_review(),
    )
    report.update(changes)
    raw = json.dumps(report).encode()
    path = tmp_path / "evaluation.json"
    path.write_bytes(raw)
    return dict(
        path=str(path),
        digest=hashlib.sha256(raw).hexdigest(),
        model="pinned-test-model",
        percent=100,
        cohort_key="batch:1",
        now=NOW,
    )


def test_passing_evaluation_and_pinned_cohort(tmp_path):
    config = artifact(tmp_path)
    assert rollout_allowed(**config)
    config["percent"] = 0
    assert not rollout_allowed(**config)


def test_untested_segment_is_never_admitted(tmp_path):
    config = artifact(tmp_path)
    assert rollout_allowed(**config, segment="technical")
    assert not rollout_allowed(**config, segment="finance_accounting:fresher")


@pytest.mark.asyncio
async def test_live_cohort_checks_domain_before_any_sdk_call(monkeypatch):
    monkeypatch.setattr(jev_client, "_approved", lambda *args, **kwargs: False)

    def forbidden(**kwargs):
        pytest.fail("Unapproved domain must not construct an SDK client")

    monkeypatch.setattr(jev_client, "AsyncTypeSafeClient", forbidden)
    provider = jev_client.JevClient("synthetic", None, "test", 1, cohort_key="batch:1")
    answers = await provider.evaluate(
        resume_text="Synthetic",
        parsed_resume_json={},
        role_name="Test",
        role_description="Test",
        required_skills=[],
        preferred_skills=[],
    )
    assert answers["needs_manual_review"].value is True


@pytest.mark.parametrize(
    "override",
    [
        {"percent": -1},
        {"percent": 101},
        {"percent": True},
        {"cohort_key": ""},
        {"model": "different"},
        {"digest": "0" * 64},
        {"path": "/not/a/file"},
        {"now": NOW + timedelta(days=31)},
        {"now": NOW - timedelta(seconds=1)},
    ],
)
def test_rollout_fails_closed(tmp_path, override):
    config = artifact(tmp_path)
    config.update(override)
    assert not rollout_allowed(**config)


@pytest.mark.parametrize(
    "change",
    [
        {"task": "interview_grading"},
        {"passed": 1},
        {"failures": ["bad"]},
        {"segments": {}},
        {"expiresAt": "2027-10-21T00:00:00+00:00"},
        {"evaluatedAt": "2026-10-21T00:00:00"},
        {"overall": {"count": 500}},
    ],
)
def test_unapproved_or_malformed_artifacts_do_not_enable_provider(tmp_path, change):
    assert not rollout_allowed(**artifact(tmp_path, **change))


def test_cohort_stable_and_nested(tmp_path):
    config = artifact(tmp_path)
    admitted = []
    for percent in [1, 10, 50, 100]:
        config["percent"] = percent
        group = {
            str(i)
            for i in range(1000)
            if rollout_allowed(**{**config, "cohort_key": str(i)})
        }
        assert not admitted or admitted[-1] <= group
        assert group == {
            str(i)
            for i in range(1000)
            if rollout_allowed(**{**config, "cohort_key": str(i)})
        }
        admitted.append(group)


def test_missing_provider_predictions_are_not_dropped():
    rows = cases()
    rows[0]["predictedScore"] = None
    rows[1]["predictedScore"] = None
    report = evaluate_cases(rows, model="test", dataset_id="synthetic", now=NOW)
    assert not report["passed"]
    assert report["overall"]["predictionCoverage"] == 58 / 60


def test_false_negatives_and_subgroup_regression_block_promotion():
    rows = cases()
    rows[0]["predictedReview"] = False
    report = evaluate_cases(rows, model="test", dataset_id="synthetic", now=NOW)
    assert "communication:reviewRecall" in report["failures"]
    assert not report["passed"]


def test_genuinely_unscorable_cases_need_no_invented_gold_score():
    rows = cases()
    for row in rows:
        if row["expectedReview"]:
            row["expectedScore"] = row["predictedScore"] = None
    report = evaluate_cases(rows, model="test", dataset_id="synthetic")
    assert report["passed"]
    assert report["overall"]["scoreLabelCount"] == 30
    assert report["overall"]["predictionCoverage"] == 1
    rows[0]["expectedReview"] = False
    with pytest.raises(ValueError):
        evaluate_cases(rows, model="test", dataset_id="synthetic")


@pytest.mark.parametrize(
    "field,value",
    [
        ("predictedScore", float("nan")),
        ("expectedScore", True),
        ("predictedReview", 1),
        ("segment", ""),
        ("id", "1"),
    ],
)
def test_bad_labels_rejected(field, value):
    rows = cases()
    rows[0][field] = value
    with pytest.raises(ValueError):
        evaluate_cases(rows, model="test", dataset_id="synthetic")


def valid_response():
    return SimpleNamespace(
        scores={
            key: SimpleNamespace(score=3.0, confidence=0.8)
            for key in jev_client.SCORE_LEVELS
        },
        choices={
            "education_quality": SimpleNamespace(choice="Strong", confidence=0.9),
            "project_tier": SimpleNamespace(choice="Good", confidence=0.8),
        },
        nouls={
            "needs_manual_review": SimpleNamespace(noul=0.2),
            "experience_mode_mismatch": SimpleNamespace(noul=0.1),
        },
    )


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1, 5, "3"])
def test_invalid_scores_cannot_be_clamped_into_valid_scores(value):
    response = valid_response()
    response.scores["skills_score"].score = value
    with pytest.raises(ValueError):
        jev_client._validated_answers(response)
    assert (
        jev_client.score_answer_to_pct(
            jev_client.JevAnswer("skills_score", "score", value)
        )
        is None
    )


def test_complete_response_keeps_probability_separate_from_confidence():
    answers = jev_client._validated_answers(valid_response())
    assert answers["needs_manual_review"].value is False
    assert answers["needs_manual_review"].confidence == 0.2


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "unknown_choice",
        "bad_probability",
        "bad_confidence",
        "duplicate_type",
    ],
)
def test_bad_schema_rejected(kind):
    response = valid_response()
    if kind == "missing":
        del response.scores["skills_score"]
    elif kind == "unknown_choice":
        response.choices["education_quality"].choice = "unsupported"
    elif kind == "bad_probability":
        response.nouls["needs_manual_review"].noul = float("nan")
    elif kind == "bad_confidence":
        response.scores["skills_score"].confidence = -1
    else:
        response.choices["skills_score"] = SimpleNamespace(
            choice="Strong", confidence=0.9
        )
    with pytest.raises(ValueError):
        jev_client._validated_answers(response)


def test_key_alone_does_not_enable_jev(monkeypatch):
    monkeypatch.setattr(jev_client.settings, "JEV_API_KEY", "synthetic")
    monkeypatch.setattr(jev_client.settings, "JEV_BULK_ATS_ROLLOUT_PERCENT", 0)
    assert isinstance(
        jev_client.get_jev_provider(cohort_key="batch:1"), jev_client.StubJevProvider
    )


@pytest.mark.asyncio
async def test_partial_sdk_result_degrades_to_manual_review(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def system_one(self, **kwargs):
            return SimpleNamespace(scores={}, choices={}, nouls={})

    monkeypatch.setattr(jev_client, "AsyncTypeSafeClient", Client)
    provider = jev_client.JevClient("synthetic", None, "test", 1)
    answers = await provider.evaluate(
        resume_text="Synthetic",
        parsed_resume_json={},
        role_name="Test",
        role_description="Test",
        required_skills=[],
        preferred_skills=[],
    )
    assert answers["needs_manual_review"].value is True
    assert all(answers[key].value is None for key in jev_client.SCORE_LEVELS)


def test_cli_report_checksum_no_overwrite_and_no_raw_cases(
    tmp_path, monkeypatch, capsys
):
    main = runpy.run_path(
        str(Path(__file__).resolve().parents[4] / "ops/evaluate_jev.py")
    )["main"]
    source, output = tmp_path / "cases.json", tmp_path / "report.json"
    source.write_text(json.dumps(cases()))
    manifest = tmp_path / "review-manifest.json"
    review = label_review("test", "synthetic-test-only")
    review["cases_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(review))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_jev.py",
            str(source),
            "--model",
            "test",
            "--dataset-id",
            "synthetic-test-only",
            "--output",
            str(output),
            "--review-manifest",
            str(manifest),
        ],
    )
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    original = output.read_bytes()
    assert "predictedScore" not in output.read_text()
    assert main() == 2
    assert output.read_bytes() == original


def test_cli_does_not_promote_bad_or_incomplete_evidence(tmp_path, monkeypatch):
    main = runpy.run_path(
        str(Path(__file__).resolve().parents[4] / "ops/evaluate_jev.py")
    )["main"]
    source, output = tmp_path / "cases.json", tmp_path / "report.json"
    source.write_text(json.dumps(cases()[:1]))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_jev.py",
            str(source),
            "--model",
            "test",
            "--dataset-id",
            "synthetic-test-only",
            "--output",
            str(output),
        ],
    )
    assert main() == 1
    assert json.loads(output.read_text())["passed"] is False
