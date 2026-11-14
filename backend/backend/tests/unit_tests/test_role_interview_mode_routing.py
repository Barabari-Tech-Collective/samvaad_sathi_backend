"""Role-created interviews must snapshot as assessments, never practice."""

import types

import pytest

from src.repository.crud import interview as interview_crud
from src.repository.crud.interview import InterviewCRUDRepository


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, profiles):
        self.profiles = profiles

    async def get(self, _model, profile_id):
        return next((p for p in self.profiles if p.id == profile_id), None)

    async def execute(self, _stmt):
        return FakeResult(self.profiles)

    def add(self, obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def repo(profiles):
    return InterviewCRUDRepository(async_session=FakeSession(profiles))


def profile(pid, mode):
    return types.SimpleNamespace(id=pid, interview_mode=mode)


@pytest.mark.asyncio
async def test_role_track_without_profile_id_resolves_the_roles_mode():
    interview = await repo([profile(7, "assessment")]).create_interview(
        user_id=1, track="Non-Tech: Sales Lead"
    )
    assert interview.interview_mode == "assessment"
    assert interview.job_profile_id == 7
    assert interview.expires_at is None


@pytest.mark.asyncio
async def test_unresolvable_role_track_is_an_assessment_not_practice():
    interview = await repo([]).create_interview(user_id=1, track="Non-Tech: Gone Role")
    assert interview.interview_mode == "assessment"
    assert interview.job_profile_id is None


@pytest.mark.asyncio
async def test_ambiguous_role_name_is_an_assessment():
    interview = await repo([profile(1, "practice"), profile(2, "assessment")]).create_interview(
        user_id=1, track="Non-Tech: Twin"
    )
    assert interview.interview_mode == "assessment"


@pytest.mark.asyncio
async def test_free_practice_track_stays_practice():
    interview = await repo([]).create_interview(user_id=1, track="Backend Developer")
    assert interview.interview_mode == "practice"
    assert interview.expires_at is not None


@pytest.mark.asyncio
async def test_explicit_profile_id_wins():
    interview = await repo([profile(3, "practice")]).create_interview(
        user_id=1, track="Non-Tech: Anything", job_profile_id=3
    )
    assert interview.interview_mode == "practice"


def test_prefix_constant_matches_the_role_flow():
    assert interview_crud.ROLE_TRACK_PREFIX == "Non-Tech:"
