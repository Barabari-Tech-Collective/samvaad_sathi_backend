from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.repository.crud.job_profile import (
    MAX_BANK_QUESTIONS_PER_LEVEL,
    JobProfileCRUDRepository,
)


def profile_with(*, level_one: int = 0, level_two: int = 0):
    return SimpleNamespace(
        questions=[SimpleNamespace(level=1) for _ in range(level_one)]
        + [SimpleNamespace(level=2) for _ in range(level_two)]
    )


def test_each_level_accepts_exactly_its_remaining_space():
    JobProfileCRUDRepository.require_level_capacity(
        profile_with(level_one=MAX_BANK_QUESTIONS_PER_LEVEL - 3, level_two=50),
        level=1,
        incoming=3,
    )


def test_one_level_rejects_overflow_without_blocking_another_level():
    profile = profile_with(level_one=MAX_BANK_QUESTIONS_PER_LEVEL - 3, level_two=50)
    with pytest.raises(HTTPException) as error:
        JobProfileCRUDRepository.require_level_capacity(
            profile, level=1, incoming=4
        )

    assert error.value.status_code == 409
    assert "only 3 more" in str(error.value.detail)
    with pytest.raises(HTTPException):
        JobProfileCRUDRepository.require_level_capacity(profile, level=2)


def test_four_full_levels_form_a_valid_200_question_bank():
    profile = SimpleNamespace(
        questions=[
            SimpleNamespace(level=level)
            for level in (1, 2, 3, 4)
            for _ in range(MAX_BANK_QUESTIONS_PER_LEVEL)
        ]
    )
    with pytest.raises(HTTPException) as error:
        JobProfileCRUDRepository.require_level_capacity(profile, level=4)

    assert error.value.status_code == 409
