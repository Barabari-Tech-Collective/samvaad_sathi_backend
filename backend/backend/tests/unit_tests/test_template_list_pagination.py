"""Template lists are paged: real total, offset, name search, and a way to open one template."""

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import fastapi
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from src.api.routes import job_profiles_v2
from src.repository.crud.job_profile import JobProfileCRUDRepository


def _profile(profile_id: int, name: str, mode: str):
    now = datetime.datetime.now(datetime.timezone.utc)
    return SimpleNamespace(
        id=profile_id,
        job_name=name,
        job_description="",
        skills=None,
        experience_level=None,
        created_at=now,
        updated_at=now,
        status="draft",
        company_name=None,
        additional_context=None,
        category=None,
        employment_type=None,
        interview_mode=mode,
        title=name,
        description="",
    )


def _client(repo):
    """The real router, with the sign-in and repository dependencies swapped for stubs."""
    app = fastapi.FastAPI()
    app.include_router(job_profiles_v2.router)
    for route in app.routes:
        if getattr(route, "path", "").startswith("/v2/") and hasattr(route, "dependant"):
            for dep in route.dependant.dependencies:
                if dep.call is job_profiles_v2.get_current_creator_user:
                    app.dependency_overrides[dep.call] = lambda: SimpleNamespace(id=1)
                elif getattr(dep.call, "__name__", "") != "get_current_creator_user":
                    app.dependency_overrides[dep.call] = lambda: repo
    return TestClient(app)


def _compile(query) -> str:
    return str(
        query.compile(
            dialect=postgresql.dialect(paramstyle="named"),
            compile_kwargs={"literal_binds": True},
        )
    )


# --- the query ---------------------------------------------------------------


def test_listing_query_pages_searches_and_orders_stably():
    sql = _compile(
        JobProfileCRUDRepository.build_listing_query(
            limit=10, offset=20, is_admin=True, interview_mode="practice", search="data"
        )
    )
    assert "LIMIT 10" in sql and "OFFSET 20" in sql
    assert "ILIKE '%data%'" in sql
    assert "interview_mode = 'practice'" in sql
    # The id breaks ties so a page boundary never repeats or skips a template.
    order = sql[sql.index("ORDER BY") :]
    assert order.index("created_at DESC") < order.index("id DESC")


def test_listing_query_without_paging_keeps_the_old_behaviour():
    sql = _compile(JobProfileCRUDRepository.build_listing_query(is_admin=True))
    assert "OFFSET" not in sql and "LIMIT" not in sql and "ILIKE" not in sql


def test_search_treats_percent_and_underscore_literally():
    pattern = JobProfileCRUDRepository._like_pattern("50%_off\\")
    assert pattern == "%50\\%\\_off\\\\%"
    # What Postgres actually receives is the bound value, with an explicit escape character.
    compiled = JobProfileCRUDRepository.build_listing_query(
        is_admin=True, search="a_b"
    ).compile(dialect=postgresql.dialect(paramstyle="named"))
    assert "%a\\_b%" in compiled.params.values()
    assert "ESCAPE" in str(compiled)


# --- the list endpoints ------------------------------------------------------


@pytest.mark.parametrize(
    "path, mode",
    [("/v2/practice-templates", "practice"), ("/v2/assessment-roles", "assessment")],
)
def test_list_returns_one_page_and_the_real_total(path, mode):
    repo = MagicMock()
    repo.list_profiles = AsyncMock(return_value=[_profile(7, "Data analyst", mode)])
    repo.count_profiles = AsyncMock(return_value=134)

    response = _client(repo).get(path, params={"limit": 10, "offset": 30, "q": "data"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 1
    assert body["total"] == 134  # the real count, not the length of this page
    repo.list_profiles.assert_awaited_once_with(
        limit=10, offset=30, is_admin=True, interview_mode=mode, search="data"
    )
    repo.count_profiles.assert_awaited_once_with(
        is_admin=True, interview_mode=mode, search="data"
    )


def test_list_defaults_are_unchanged_for_existing_callers():
    repo = MagicMock()
    repo.list_profiles = AsyncMock(return_value=[])
    repo.count_profiles = AsyncMock(return_value=0)
    assert _client(repo).get("/v2/practice-templates").status_code == 200
    repo.list_profiles.assert_awaited_once_with(
        limit=100, offset=0, is_admin=True, interview_mode="practice", search=None
    )


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"q": "x" * 101}],
)
def test_list_rejects_out_of_range_paging(params):
    repo = MagicMock()
    repo.list_profiles = AsyncMock(return_value=[])
    repo.count_profiles = AsyncMock(return_value=0)
    assert _client(repo).get("/v2/assessment-roles", params=params).status_code == 422
    repo.list_profiles.assert_not_awaited()


# --- opening one template ----------------------------------------------------


def test_one_template_can_be_opened_by_id():
    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value=_profile(9, "Support round", "practice"))
    response = _client(repo).get("/v2/practice-templates/9")
    assert response.status_code == 200, response.text
    assert response.json()["jobName"] == "Support round"


@pytest.mark.parametrize(
    "path, found_mode",
    [
        ("/v2/practice-templates/9", "assessment"),  # an assessment is not a mock template
        ("/v2/assessment-roles/9", "practice"),
    ],
)
def test_opening_a_template_of_the_other_purpose_is_a_404(path, found_mode):
    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value=_profile(9, "Other", found_mode))
    assert _client(repo).get(path).status_code == 404


def test_opening_a_missing_template_is_a_404():
    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value=None)
    assert _client(repo).get("/v2/assessment-roles/123").status_code == 404
