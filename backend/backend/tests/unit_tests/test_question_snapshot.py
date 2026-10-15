"""An interview's questions must never change after they are generated.

Students reported being asked one question and then, after a refresh, seeing a
different one. Two defects produced that, and this module pins both shut.

The first was serialization. Three endpoints insert into `interview_question`
for the same interview - `interviews:generate-questions`,
`interviews-v2:generate-questions` and
`interviews-v2:generate-non-tech-questions` - and only the middle one took an
advisory lock, so the other two serialized against nothing. A client retry, or
any mix of v1 and v2 traffic, could generate and persist two full sets.

The second was response assembly, and it is the one students actually saw. The
reply was built by zipping freshly generated LLM output against the rows in the
database *by list index*. When a concurrent request had already persisted its
own set, the reply carried this request's text under the other request's
question IDs - so the student answered text that was never stored against those
IDs, and a refresh (which reads the rows) showed different questions.

`test_every_question_writer_takes_the_lock` is the structural guard: it fails
when a fourth writer is added without locking, which is the realistic way this
regresses. The rest assert the builder cannot reintroduce the crossover.
"""

import ast
import pathlib
import types
from typing import cast

import pytest

from src.models.db.interview_question import InterviewQuestion
from src.services.question_snapshot import (
    QUESTION_GENERATION_LOCK_NAMESPACE,
    question_items_from_persisted,
)

ROUTES_DIR = pathlib.Path(__file__).resolve().parents[2] / "src" / "api" / "routes"

#: `create_batch` call sites that cannot race, with the reason they are exempt.
#: Keyed by the enclosing function name so that renaming the function forces a
#: fresh look rather than silently widening the exemption.
_EXEMPT_WRITERS = {
    # Always inserts against an interview it just created via create_interview(),
    # whose id no other request can know yet, so there is nothing to race with.
    "create_structure_practice_session",
    # Bulk ATS creates resume screening batches, not interview questions.
    "create_batch",
}


def _question_writers(source: str) -> dict[str, bool]:
    """Map each function that inserts interview questions to whether it locks.

    A writer is a function containing a `question_repo.create_batch(...)` call;
    it locks if the same function also calls `lock_question_generation(...)`.
    Both are matched on the attribute/function name so that an import alias or
    a differently named repository variable does not hide a writer.
    """
    writers: dict[str, bool] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        writes = False
        locks = False
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if isinstance(func, ast.Attribute) and func.attr == "create_batch":
                writes = True
            elif isinstance(func, ast.Name) and func.id == "lock_question_generation":
                locks = True
        if writes:
            writers[node.name] = locks
    return writers


def test_every_question_writer_takes_the_lock():
    """Adding a generation endpoint without a lock must fail here, not in production."""
    unlocked = []
    found_any = False
    for path in sorted(ROUTES_DIR.glob("*.py")):
        for name, locks in _question_writers(path.read_text()).items():
            found_any = True
            if not locks and name not in _EXEMPT_WRITERS:
                unlocked.append(f"{path.name}::{name}")

    assert found_any, (
        "Found no interview-question writers at all. The detection in "
        "_question_writers has probably gone stale - verify it before trusting a pass."
    )
    assert not unlocked, (
        "These functions insert interview questions without serializing first:\n  "
        + "\n  ".join(unlocked)
        + "\nCall lock_question_generation(session, interview_id=...) before reading "
        "whether questions exist, or add a justified entry to _EXEMPT_WRITERS."
    )


def _row(question_id: int, text: str, **overrides) -> InterviewQuestion:
    """A stand-in for a persisted InterviewQuestion row.

    The builder only ever reads attributes, so a namespace keeps the test honest
    about which fields it actually depends on.
    """
    fields = {
        "id": question_id,
        "text": text,
        "topic": "topic-" + str(question_id),
        "category": "tech",
        "is_follow_up": False,
        "parent_question_id": None,
        "follow_up_strategy": "strategy",
        "audio_url": None,
    }
    fields.update(overrides)
    return cast(InterviewQuestion, types.SimpleNamespace(**fields))


def test_text_always_belongs_to_the_id_it_is_returned_with():
    """The exact crossover students saw: text from one set, IDs from another."""
    persisted = [_row(101, "What the database stores"), _row(102, "Second stored question")]

    items = question_items_from_persisted(persisted)

    assert [item["interviewQuestionId"] for item in items] == [101, 102]
    assert [item["text"] for item in items] == [
        "What the database stores",
        "Second stored question",
    ]
    # Every item's text must be its own row's text, never a neighbour's.
    for item, row in zip(items, persisted):
        assert item["text"] == row.text
        assert item["interviewQuestionId"] == row.id


def test_builder_cannot_be_handed_generated_text():
    """The crossover is impossible by signature, not by discipline.

    The old code could only mix text because it had the LLM items in scope at
    the point of assembly. If someone adds a parameter that lets generated text
    back in, this fails and they have to justify it.
    """
    with pytest.raises(TypeError):
        question_items_from_persisted([_row(1, "stored")], items=[{"text": "generated"}])  # type: ignore[call-arg]


def test_difficulty_is_uniform_so_a_refresh_cannot_disagree():
    """Difficulty is not a column on interview_question.

    The v2 generator used to report per-question difficulty from the LLM while
    the cached branch reported None, so the same interview described itself
    differently before and after a refresh. The interview's difficulty is the
    only value that survives a re-read.
    """
    items = question_items_from_persisted(
        [_row(1, "a"), _row(2, "b")], difficulty="hard"
    )

    assert [item["difficulty"] for item in items] == ["hard", "hard"]


def test_supplements_attach_by_question_id_not_position():
    """Supplements are keyed by question id, so a missing one cannot shift the rest."""
    items = question_items_from_persisted(
        [_row(10, "a"), _row(20, "b"), _row(30, "c")],
        supplements={30: "supplement-for-30", 10: "supplement-for-10"},
    )

    by_id = {item["interviewQuestionId"]: item["supplement"] for item in items}
    assert by_id == {10: "supplement-for-10", 20: None, 30: "supplement-for-30"}


def test_empty_question_set_is_not_an_error():
    assert question_items_from_persisted([]) == []


def test_row_fields_are_passed_through_untouched():
    """Follow-up wiring must survive assembly, or the report mis-parents answers."""
    items = question_items_from_persisted(
        [_row(7, "follow up", is_follow_up=True, parent_question_id=3, audio_url="s3://a.mp3")]
    )

    assert items[0]["isFollowUp"] is True
    assert items[0]["parentQuestionId"] == 3
    assert items[0]["audioUrl"] == "s3://a.mp3"
    assert items[0]["followUpStrategy"] == "strategy"
    assert items[0]["topic"] == "topic-7"
    assert items[0]["category"] == "tech"


def test_lock_namespace_is_stable():
    """The namespace is part of the on-the-wire lock identity.

    Two application versions that disagree about it do not exclude each other,
    so changing this value needs a deliberate deploy plan rather than a casual
    edit.
    """
    assert QUESTION_GENERATION_LOCK_NAMESPACE == 1
