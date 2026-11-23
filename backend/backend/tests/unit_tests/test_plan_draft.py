import pytest

from src.services import writing_assist


def _stub(monkeypatch, **payload):
    async def fake(model, **k):
        return model(**payload), None, None, "m"

    monkeypatch.setattr(writing_assist, "structured_output", fake)


@pytest.mark.asyncio
async def test_plan_draft_drops_unknown_drills_and_bad_titles(monkeypatch):
    _stub(
        monkeypatch,
        title="  Calm HR answers  ",
        steps=[
            {"kind": "drill", "title": "Warm up", "instructions": "Chat easily.", "module": "warm_up"},
            {"kind": "drill", "title": "Made-up drill", "module": "not_a_drill"},
            {"kind": "drill", "title": "No module"},
            {"kind": "reflection", "title": "Write it down", "instructions": "What will you change?"},
            {"kind": "reflection", "title": "x"},
        ],
    )
    out = await writing_assist.plan_draft(goal="Speak calmly in HR rounds", weeks=2)
    assert out["title"] == "Calm HR answers"
    assert [s["title"] for s in out["steps"]] == ["Warm up", "Write it down"]
    assert out["steps"][0]["module"] == "warm_up"
    assert out["steps"][1]["module"] is None


@pytest.mark.asyncio
async def test_plan_draft_turns_an_unknown_kind_into_a_reflection(monkeypatch):
    _stub(
        monkeypatch,
        title="Plan",
        steps=[
            {"kind": "quiz", "title": "Think about it", "module": "pace"},
            {"kind": "reflection", "title": "Notes", "module": "pace"},
        ],
    )
    out = await writing_assist.plan_draft(goal="Be clearer", weeks=1)
    assert [(s["kind"], s["module"]) for s in out["steps"]] == [
        ("reflection", None),
        ("reflection", None),
    ]


@pytest.mark.asyncio
async def test_plan_draft_caps_at_seven_steps(monkeypatch):
    _stub(
        monkeypatch,
        title="Long plan",
        steps=[
            {"kind": "drill", "title": f"Step {i}", "module": "structure"} for i in range(12)
        ],
    )
    out = await writing_assist.plan_draft(goal="Practise a lot", weeks=4)
    assert len(out["steps"]) == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"title": "Plan", "steps": [{"kind": "drill", "title": "Only one", "module": "pace"}]},
        {"title": "ab", "steps": [
            {"kind": "drill", "title": "One", "module": "pace"},
            {"kind": "drill", "title": "Two", "module": "pace"},
        ]},
        {"title": "Plan", "steps": []},
    ],
)
async def test_plan_draft_returns_nothing_when_too_little_survives(monkeypatch, payload):
    _stub(monkeypatch, **payload)
    assert await writing_assist.plan_draft(goal="Anything at all", weeks=2) is None


@pytest.mark.asyncio
async def test_plan_draft_returns_nothing_without_ai(monkeypatch):
    async def fake(*a, **k):
        return None, None, None, "m"

    monkeypatch.setattr(writing_assist, "structured_output", fake)
    assert await writing_assist.plan_draft(goal="Anything at all", weeks=2) is None
