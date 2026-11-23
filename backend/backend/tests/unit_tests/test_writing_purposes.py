import typing

from src.api.routes.writing_assist import Ask
from src.services import writing_assist


def test_every_allowed_purpose_has_its_own_instruction():
    allowed = set(typing.get_args(Ask.model_fields["purpose"].annotation))
    assert allowed == set(writing_assist.PURPOSES)


def test_unknown_purpose_is_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Ask(text="hello there", purpose="something_else")


def test_new_purposes_are_accepted():
    for purpose in ("instruction", "reason", "description", "question", "message"):
        assert Ask(text="hello there", purpose=purpose).purpose == purpose
