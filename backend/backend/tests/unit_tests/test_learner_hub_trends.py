import datetime as dt
from types import SimpleNamespace

from src.services import learner_hub as lh


def test_assignment_and_coaching_have_a_daily_trend():
    start = dt.date(2026, 11, 1)
    d0, d1 = start, start + dt.timedelta(days=1)
    people = [
        SimpleNamespace(id=i, improvement=None, last_score=None, knowledge=None, speech=None,
                        status="active", stale=False)
        for i in (1, 2, 3)
    ]
    context = SimpleNamespace(
        start=start, days=3,
        assignments=[(1, True, d0), (2, False, d0), (3, True, d1), (99, True, d1)],
        coaching=[(1, False, d1)],
    )
    out = lh.outcomes(context, people, {"scored": 0, "active": 0})
    # Day 1: 1 of 2 finished; day 2: 1 of 1 (learner 99 is out of scope); day 3: nothing due.
    assert out["assignments"]["series"] == [50, 100, None]
    assert out["coaching"]["series"] == [None, 0, None]
    assert out["coaching"]["open"] == 1


def test_coaching_item_edit_is_bounded():
    import pytest
    from pydantic import ValidationError

    from src.api.routes.coaching_hub import ITEM_MODELS, ItemEdit

    assert set(ITEM_MODELS) == {"c", "m", "p"}
    ItemEdit(expected_version=1, reason="Practise speaking slowly", note="", due_date=None)
    with pytest.raises(ValidationError):
        ItemEdit(expected_version=1, reason="short")
    with pytest.raises(ValidationError):
        ItemEdit(expected_version=0)
