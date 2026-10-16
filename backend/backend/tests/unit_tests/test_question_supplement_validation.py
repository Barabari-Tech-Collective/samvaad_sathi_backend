from src.api.routes.interviews_v2 import _filter_valid_supplements
from src.models.schemas.interview import QuestionSupplementOut


def _supplement(
    question_id: int,
    *,
    supplement_type: str,
    format: str,
    content: str,
) -> QuestionSupplementOut:
    return QuestionSupplementOut(
        question_id=question_id,
        supplement_type=supplement_type,
        format=format,
        content=content,
    )


def test_invalid_optional_supplements_do_not_block_question_delivery() -> None:
    items = {
        1: _supplement(
            1,
            supplement_type="code",
            format="python",
            content="print('valid')",
        ),
        2: _supplement(
            2,
            supplement_type="diagram",
            format="mermaid",
            content="flowchart LR\n  UI --> API",
        ),
        3: _supplement(
            3,
            supplement_type="diagram",
            format="mermaid",
            content="This is not Mermaid syntax",
        ),
        4: _supplement(
            4,
            supplement_type="text",
            format="markdown",
            content="Unsupported supplement type",
        ),
    }

    assert _filter_valid_supplements(items=items, source="test") == {
        1: items[1],
        2: items[2],
    }


def test_extended_mermaid_diagrams_are_not_rejected() -> None:
    diagram = _supplement(
        5,
        supplement_type="diagram",
        format="mermaid",
        content="erDiagram\n  USER ||--o{ INTERVIEW : takes",
    )

    assert _filter_valid_supplements(items={5: diagram}, source="test") == {5: diagram}
