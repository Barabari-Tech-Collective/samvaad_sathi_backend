import json
import logging
import re

import pytest

from src.config.json_logging import JsonFormatter, MaskingFormatter, mask_phones

PHONE = re.compile(r"(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}|\+\d{11,15}")


def _record(msg, **extra):
    r = logging.LogRecord("src.worker.tasks", logging.ERROR, __file__, 1, msg, None, None)
    for k, v in extra.items():
        setattr(r, k, v)
    return r


def test_json_line_has_the_fields_log_shipping_matches_on():
    line = json.loads(JsonFormatter().format(_record("Sampark callback failed request_id=abc-1 for student STU-0001")))
    assert line["level"] == "ERROR"
    assert line["service"] == "samvaad-backend"
    assert line["requestId"] == "abc-1"
    assert line["studentUniqueId"] == "STU-0001"


def test_extra_fields_win_over_text():
    line = json.loads(JsonFormatter().format(_record("x", request_id="r9", student_unique_id="S9")))
    assert (line["requestId"], line["studentUniqueId"]) == ("r9", "S9")


@pytest.mark.parametrize("phone", ["+919876543210", "919876543210", "98765 43210", "+91-98765-43210", "+447911123456"])
def test_phone_numbers_never_reach_a_log_line(phone):
    assert not PHONE.search(JsonFormatter().format(_record(f"sending to {phone}")))
    assert not PHONE.search(MaskingFormatter("%(message)s").format(_record(f"sending to {phone}")))


def test_numeric_ids_are_left_alone():
    assert mask_phones("analysis 1700000000017 score 72") == "analysis 1700000000017 score 72"
