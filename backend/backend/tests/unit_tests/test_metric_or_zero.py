"""SQL AVG() returns Decimal; it must be read as a number, not as missing."""

import decimal

from src.api.routes.analytics_v2 import _metric_or_zero


def test_decimal_average_is_kept():
    assert _metric_or_zero(decimal.Decimal("449.5")) == 449.5
    assert _metric_or_zero(decimal.Decimal("33.8149"), digits=2) == 33.81


def test_missing_values_are_zero():
    assert _metric_or_zero(None) == 0
    assert _metric_or_zero(float("nan")) == 0


def test_metric_or_none_keeps_no_data_distinct_from_zero():
    # A learner with no scored interview must not show "Average score 0".
    from src.api.routes.analytics_v2 import _metric_or_none

    assert _metric_or_none(None) is None
    assert _metric_or_none(float("nan")) is None
    assert _metric_or_none(0) == 0
    assert _metric_or_none(decimal.Decimal("61.456"), digits=2) == 61.46
