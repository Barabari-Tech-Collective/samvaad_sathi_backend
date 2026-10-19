from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import fastapi
import pytest
from sqlalchemy.dialects import postgresql

from src.api.routes.course_workspace import (
    CourseAssignment,
    assign_member_courses,
    course_students,
)
from src.models.db.user import User
from src.services.course_access import (
    CourseGrant,
    membership_grants,
    require_course,
    require_global_legacy_analytics,
    student_predicate,
)


def user(admin=False):
    return User(id=1, email="staff@example.org", is_admin=admin, is_super_admin=False)


def test_course_and_college_grants_do_not_form_a_cross_product():
    grants = membership_grants(
        [
            (
                SimpleNamespace(
                    course_ids=["development"],
                    colleges=["College A"],
                    access_role="facilitator",
                ),
                None,
            ),
            (
                SimpleNamespace(
                    course_ids=["design"],
                    colleges=["College B"],
                    access_role="facilitator",
                ),
                None,
            ),
        ]
    )
    assert any(g.allows("development", "College A") for g in grants)
    assert not any(g.allows("development", "College B") for g in grants)
    assert not any(g.allows("design", "College A") for g in grants)
    sql = str(
        student_predicate("development", grants, global_access=False).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "enrolled_course_ids" in sql and "university" in sql


@pytest.mark.parametrize("ids", [None, [], "development"])
def test_missing_or_invalid_course_assignment_grants_nothing(ids):
    assert (
        membership_grants(
            [SimpleNamespace(course_ids=ids, colleges=None, access_role="facilitator")]
        )
        == ()
    )


def test_junior_facilitator_without_college_does_not_get_all_colleges():
    assert (
        membership_grants(
            [
                SimpleNamespace(
                    course_ids=["development"],
                    colleges=None,
                    access_role="Junior Facilitator",
                )
            ]
        )
        == ()
    )


def test_malformed_college_scope_is_not_unrestricted():
    assert (
        membership_grants(
            [
                SimpleNamespace(
                    course_ids=["development"],
                    colleges="College A",
                    access_role="facilitator",
                )
            ]
        )
        == ()
    )


@pytest.mark.asyncio
async def test_restoring_membership_requires_course_reapproval():
    from src.repository.crud.team_v2 import TeamCRUDRepository

    session = AsyncMock()
    result = MagicMock()
    member = SimpleNamespace(
        status="deleted", course_ids=["development"], removed_date=None
    )
    result.scalar_one_or_none.return_value = member
    session.execute.return_value = result
    repo = TeamCRUDRepository(async_session=session)
    assert await repo.restore_member(2, 4) is True
    assert member.course_ids == []


@pytest.mark.asyncio
async def test_interview_course_requires_enrollment_before_creating_evidence():
    from src.repository.crud.interview import InterviewCRUDRepository

    session = AsyncMock()
    session.get.return_value = SimpleNamespace(enrolled_course_ids=["design"])
    repo = InterviewCRUDRepository(async_session=session)
    with pytest.raises(fastapi.HTTPException) as exc:
        await repo.create_interview(user_id=7, track="general", course_id="development")
    assert exc.value.status_code == 403
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_interview_keeps_the_verified_course_snapshot():
    from src.repository.crud.interview import InterviewCRUDRepository

    session = AsyncMock()
    session.add = MagicMock()
    session.get.return_value = SimpleNamespace(enrolled_course_ids=["development"])
    repo = InterviewCRUDRepository(async_session=session)
    interview = await repo.create_interview(
        user_id=7, track="general", course_id="development"
    )
    assert interview.course_id == "development"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_old_practice_context_cannot_bypass_course_scope():
    from src.api.routes.practice_dashboard import _context

    with patch(
        "src.api.routes.practice_dashboard.get_user_active_memberships",
        AsyncMock(
            return_value=[
                (
                    SimpleNamespace(access_role="facilitator"),
                    SimpleNamespace(name="Program"),
                )
            ]
        ),
    ):
        with pytest.raises(fastapi.HTTPException) as exc:
            await _context(user(), AsyncMock())
    assert exc.value.status_code == 403


def test_unknown_course_is_denied_even_if_another_course_is_allowed():
    with pytest.raises(fastapi.HTTPException) as exc:
        require_course(
            "design", (CourseGrant(frozenset({"development"}), None),), user()
        )
    assert exc.value.status_code == 403


@pytest.mark.parametrize("ids", [["*"], [""], ["  "]])
def test_scope_payload_rejects_wildcards_and_blank_ids(ids):
    with pytest.raises(ValueError):
        CourseAssignment(course_ids=ids, reason="Assigned teaching responsibility")


def test_scope_change_requires_a_real_reason_and_deduplicates_ids():
    with pytest.raises(ValueError):
        CourseAssignment(course_ids=["dev"], reason="               ")
    assert CourseAssignment(
        course_ids=[" dev ", "dev"], reason="Assigned teaching responsibility"
    ).course_ids == ["dev"]


@pytest.mark.asyncio
async def test_staff_cannot_escalate_course_access_or_mutate_database():
    session = AsyncMock()
    with pytest.raises(fastapi.HTTPException) as exc:
        await assign_member_courses(
            4,
            CourseAssignment(course_ids=["design"], reason="Attempted scope expansion"),
            user(),
            session,
        )
    assert exc.value.status_code == 403
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_aggregations_are_not_a_scope_bypass():
    with pytest.raises(fastapi.HTTPException):
        await require_global_legacy_analytics(user())
    await require_global_legacy_analytics(user(admin=True))


@pytest.mark.asyncio
async def test_roster_query_limits_consent_enrollment_evidence_and_pagination():
    session = AsyncMock()
    count = MagicMock()
    count.scalar_one.return_value = 0
    rows = MagicMock()
    rows.all.return_value = []
    session.execute.side_effect = [count, rows]
    grant = CourseGrant(frozenset({"development"}), frozenset({"college a"}))
    member = SimpleNamespace(access_role="facilitator")
    with (
        patch(
            "src.api.routes.course_workspace.grants_for",
            AsyncMock(return_value=(grant,)),
        ),
        patch(
            "src.api.routes.course_workspace.get_user_active_memberships",
            AsyncMock(return_value=[(member, SimpleNamespace(name="Program"))]),
        ),
    ):
        result = await course_students("development", 2, 10, user(), session)
    assert result["items"] == [] and result["page"] == 2
    query = str(
        session.execute.call_args_list[-1].args[0].compile(dialect=postgresql.dialect())
    )
    for field in [
        "enrolled_course_ids",
        "course_id",
        "withdrawn_at",
        "expires_at",
        "OFFSET",
        "LIMIT",
    ]:
        assert field in query
    assert "transcription" not in query and "audio_url" not in query


def test_all_legacy_v2_routes_carry_scope_guard():
    from src.api.routes.analytics_v2 import router

    assert router.routes
    for route in router.routes:
        assert "legacy_analytics_guard" in {
            dep.call.__name__ for dep in route.dependant.dependencies
        }


def test_assignment_version_changes_with_scope_or_membership_context():
    import datetime
    from src.api.routes.course_workspace import assignment_version

    member = SimpleNamespace(
        course_ids=["1"],
        colleges=["A"],
        access_role="facilitator",
        status="active",
        team_id=1,
        domain="Program",
        purposes=["facilitator_coaching"],
        updated_at=datetime.datetime.now(datetime.timezone.utc),
    )
    original = assignment_version(member)
    assert len(original) == 64
    for key, value in [
        ("course_ids", ["2"]),
        ("colleges", ["B"]),
        ("domain", "Placement"),
        ("purposes", []),
        ("access_role", "creator"),
        ("status", "removed"),
    ]:
        before = getattr(member, key)
        setattr(member, key, value)
        assert assignment_version(member) != original
        setattr(member, key, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("expected,status", [(None, 428), ("0" * 64, 409)])
async def test_stale_or_missing_assignment_version_cannot_overwrite(expected, status):
    from src.api.routes.course_workspace import assignment_version

    member = SimpleNamespace(
        course_ids=["1"],
        colleges=["A"],
        access_role="facilitator",
        status="active",
        team_id=1,
        domain="Program",
        purposes=[],
        updated_at=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = member
    session = AsyncMock()
    session.execute.return_value = result
    with pytest.raises(fastapi.HTTPException) as error:
        await assign_member_courses(
            1,
            CourseAssignment(
                course_ids=["2"],
                reason="New teaching responsibility",
                expected_version=expected,
            ),
            user(admin=True),
            session,
        )
    assert error.value.status_code == status
    assert member.course_ids == ["1"]
    session.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_course_history_rechecks_scope_and_hides_unavailable_student():
    from src.api.routes import course_workspace as workspace

    session = AsyncMock()
    result = MagicMock()
    result.one_or_none.return_value = None
    session.execute.return_value = result
    with patch.object(workspace, "_shared_student_predicate", AsyncMock(return_value=User.id > 0)) as authorize:
        with pytest.raises(fastapi.HTTPException) as error:
            await workspace.student_course_practice("course-a", 77, 1, 25, user(), session)
    assert error.value.status_code == 404
    authorize.assert_awaited_once()
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_course_history_filters_completed_practice_and_whitelists_fields():
    from src.api.routes import course_workspace as workspace

    session = AsyncMock()
    student_result, count_result, rows_result = MagicMock(), MagicMock(), MagicMock()
    student_result.one_or_none.return_value = SimpleNamespace(id=77, name="Student", university="College A")
    count_result.scalar_one.return_value = 26
    rows_result.all.return_value = [SimpleNamespace(id=9, track="Communication", difficulty="medium", completed_at=None, duration_seconds=120, report_json={"private": "secret"})]
    session.execute.side_effect = [student_result, count_result, rows_result]
    with patch.object(workspace, "_shared_student_predicate", AsyncMock(return_value=User.id > 0)):
        response = await workspace.student_course_practice("course-a", 77, 2, 25, user(), session)
    assert response["total"] == 26 and response["page"] == 2
    assert set(response["items"][0]) == {"id", "track", "difficulty", "completed_at", "duration_seconds"}
    query = session.execute.call_args_list[-1].args[0].compile(dialect=postgresql.dialect())
    assert query.params["course_id_1"] == "course-a"
    assert query.params["user_id_1"] == 77
    assert query.params["interview_mode_1"] == "practice"
    assert query.params["status_1"] == "completed"
    assert 25 in query.params.values()


@pytest.mark.asyncio
async def test_course_history_stops_before_read_when_scope_rejected():
    from src.api.routes import course_workspace as workspace

    session = AsyncMock()
    with patch.object(workspace, "_shared_student_predicate", AsyncMock(side_effect=fastapi.HTTPException(403, "Denied"))):
        with pytest.raises(fastapi.HTTPException):
            await workspace.student_course_practice("other", 77, 1, 25, user(), session)
    session.execute.assert_not_awaited()
