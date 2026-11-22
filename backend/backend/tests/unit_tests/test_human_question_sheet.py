import datetime as dt
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.api.routes import human_scorecards as hs
from src.api.routes.writing_assist import Ask
from src.services import writing_assist


def _row(**kw):
    base = dict(
        id=1, host_id=10, interviewer_id=None, status="scheduled", result_released_at=None,
        question_sheet=[
            {"id": "q1", "text": "Tell me about your project", "area": "Projects"},
            {"id": "q2", "text": "How would you sort a list?", "area": "Technical"},
        ],
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _user(uid=10, admin=False, perms=None):
    return SimpleNamespace(
        id=uid, is_admin=admin, is_super_admin=False, role="admin" if admin else None,
        feature_permissions=perms if perms is not None else {"human_interviews": ["read", "update"]},
    )


def test_answers_follow_the_sheet_and_keep_removed_ones():
    card = SimpleNamespace(answers=[
        {"question_id": "q2", "question": "How would you sort a list?", "score": 6, "remarks": "Clear"},
        {"question_id": "old", "question": "Removed question", "score": 3, "remarks": None},
    ])
    out = hs.sheet_answers(card, _row())
    assert [a["question_id"] for a in out] == ["q1", "q2", "old"]
    assert out[0]["score"] is None and out[1]["score"] == 6 and out[1]["area"] == "Technical"


def test_scores_are_one_to_seven():
    hs.Answer(question_id="q1", question="Tell me", score=7)
    for bad in (0, 8):
        with pytest.raises(ValidationError):
            hs.Answer(question_id="q1", question="Tell me", score=bad)


def test_sheet_ids_and_size_are_bounded():
    with pytest.raises(ValidationError):
        hs.SheetBody(questions=[{"id": str(i), "text": "Question text"} for i in range(31)])
    with pytest.raises(ValidationError):
        hs.GenerateBody(count=2)


def test_editing_rules(monkeypatch):
    monkeypatch.setattr(hs.service, "permitted", lambda user, op: True)
    monkeypatch.setattr(hs, "is_admin_user", lambda u: bool(u.is_admin))
    monkeypatch.setattr(hs, "can_review", lambda u: bool(u.is_admin))
    me, other, admin = _user(), _user(uid=99), _user(uid=5, admin=True)
    row = _row()
    # The interviewer can write on the interview day, before marking it completed.
    assert hs.may_edit(None, row, me)
    assert not hs.may_edit(None, row, other)
    # An approved card is still editable until it is shared.
    approved = SimpleNamespace(status="approved")
    assert hs.may_edit(approved, _row(status="completed"), me)
    # While waiting for review only a reviewer may change it.
    submitted = SimpleNamespace(status="submitted")
    assert not hs.may_edit(submitted, _row(status="completed"), me)
    assert hs.may_edit(submitted, _row(status="completed"), admin)
    # Never after the result is shared, nor for a cancelled round.
    assert not hs.may_edit(approved, _row(status="completed", result_released_at=dt.datetime.now()), admin)
    assert not hs.may_edit(None, _row(status="cancelled"), me)


def test_rephrase_request_is_bounded():
    assert Ask(text="  good work  ", purpose="learner_feedback").text == "good work"
    with pytest.raises(ValidationError):
        Ask(text="x" * 4001)
    with pytest.raises(ValidationError):
        Ask(text="fine text", purpose="anything")


@pytest.mark.asyncio
async def test_rephrase_returns_none_without_ai(monkeypatch):
    async def fake(*a, **k):
        return None, None, None, "m"

    monkeypatch.setattr(writing_assist, "structured_output", fake)
    assert await writing_assist.rephrase("some text", "general") is None
    assert await writing_assist.question_sheet(
        title="Round 1", role=None, notes=None, focus=None, count=5
    ) is None


@pytest.mark.asyncio
async def test_question_sheet_trims_and_caps(monkeypatch):
    async def fake(model, **k):
        return model(questions=[
            {"text": "  Walk me through your final-year project.  ", "area": "Projects"},
            {"text": "Hi", "area": None},
            *[{"text": f"Question number {i}", "area": "Technical"} for i in range(10)],
        ]), None, None, "m"

    monkeypatch.setattr(writing_assist, "structured_output", fake)
    out = await writing_assist.question_sheet(title="R1", role="Analyst", notes=None, focus=None, count=4)
    assert len(out) == 4 and out[0]["text"].startswith("Walk me")
