"""Live verification against DeepSeek (2026-09-09) showed the LLM can return
the same `criteria` field shaped two different ways across calls for the
identical schema - as a plain number (`{"correctness": 80}`) or as a nested
object (`{"correctness": {"score": 80, "reasons": [...]}}`). The schema is
deliberately typed loosely (dict[str, Any] in llm.py) to tolerate this, but
several downstream consumers assumed only the nested shape and called
.get("score") directly on the value, which crashes with AttributeError on a
plain int. This is not DeepSeek-specific - the schema never strictly
enforced one shape for either provider.
"""

from src.services.analytics import _criteria_score as analytics_criteria_score
from src.services.report import _criteria_score as report_criteria_score


def test_analytics_criteria_score_handles_plain_number():
    criteria = {"correctness": 80}
    assert analytics_criteria_score(criteria, "correctness") == 80.0


def test_analytics_criteria_score_handles_nested_object():
    criteria = {"correctness": {"score": 80, "reasons": ["good"]}}
    assert analytics_criteria_score(criteria, "correctness") == 80.0


def test_analytics_criteria_score_handles_missing_key():
    assert analytics_criteria_score({}, "correctness") is None


def test_analytics_criteria_score_handles_non_dict_criteria():
    assert analytics_criteria_score(None, "correctness") is None
    assert analytics_criteria_score("not a dict", "correctness") is None


def test_report_criteria_score_handles_plain_number():
    criteria = {"clarity": 75}
    assert report_criteria_score(criteria, "clarity") == 75.0


def test_report_criteria_score_handles_nested_object():
    criteria = {"clarity": {"score": 75, "reasons": ["clear"]}}
    assert report_criteria_score(criteria, "clarity") == 75.0


def test_report_criteria_score_handles_missing_key():
    assert report_criteria_score({}, "clarity") is None
