from types import SimpleNamespace
import pytest
from src.api.routes.interviews import history_percentages


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"scoreSummary": None},
        {"scoreSummary": {"knowledgeCompetence": []}},
        {"metrics": {"speechStructure": "bad"}},
    ],
)
def test_malformed_legacy_reports_have_no_invented_score(payload):
    assert history_percentages([SimpleNamespace(report_json=payload)]) == (None, None)


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, 0),
        (100, 100),
        (33.5, 33.5),
        (-1, None),
        (101, None),
        (float("nan"), None),
        (float("inf"), None),
        (True, None),
        ("0", None),
        (10**400, None),
    ],
)
def test_only_bounded_numeric_scores_are_preserved(value, expected):
    assert history_percentages(
        [
            SimpleNamespace(
                report_json={
                    "scoreSummary": {"knowledgeCompetence": {"percentage": value}}
                }
            )
        ]
    ) == (expected, None)


def test_legacy_metrics_remain_supported():
    assert history_percentages(
        [
            SimpleNamespace(
                report_json={"metrics": {"speechStructure": {"averagePct": 65}}}
            )
        ]
    ) == (None, 65)
