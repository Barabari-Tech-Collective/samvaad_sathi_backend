"""Real SQL tests for the staff Interviews area: who sees what, then every figure and action."""

import datetime as dt
import uuid

import fastapi
import httpx
import pytest
import sqlalchemy as sa

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes import interview_hub as routes
from src.models.db.assessment_assignment import AssessmentAssignment
from src.models.db.human_interview import HumanInterview
from src.models.db.human_scorecard import HumanScorecard
from src.models.db.integrity_case import IntegrityCase
from src.models.db.interview import Interview
from src.models.db.interview_question import InterviewQuestion
from src.models.db.job_profile import JobProfile
from src.models.db.job_profile_question import JobProfileQuestion
from src.models.db.notification import Notification, NotificationDelivery
from src.models.db.question_attempt import QuestionAttempt
from src.models.db.student_placement import StudentPlacement
from src.models.db.summary_report import SummaryReport
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.repository.crud.job_profile import JobProfileCRUDRepository
from src.services import learner_hub as lh
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401

NOW = dt.datetime.now(dt.timezone.utc)
AUTHOR = {"interview_authoring": ["read", "create", "update", "generate"]}
PUBLISHER = {"interview_authoring": ["read", "create", "update", "publish"], "analytics": ["read"]}


def report(k, s):
    return {"scoreSummary": {"knowledgeCompetence": {"percentage": k}, "speechAndStructure": {"percentage": s}}, "overallScoreSummary": {"knowledgeCompetence": {"percentage": k}, "speechStructure": {"percentage": s}}}


def template(name, status, parent=None, per=1, mode="assessment", published=None, courses=None):
    return JobProfile(
        job_name=name, job_description="x" * 30, status=status, interview_mode=mode,
        questions_per_interview=per, parent_profile_id=parent, published_at=published, course_ids=courses,
    )


def questions(profile_id, per_level=1):
    return [
        JobProfileQuestion(
            job_profile_id=profile_id, question_text=f"L{level} q{n} of {profile_id}", level=level,
            difficulty="easy", question_type="technical",
        )
        for level in (1, 2, 3, 4)
        for n in range(per_level)
    ]


@pytest.fixture
async def hc(db):
    _, factory, _ = db
    lh._cache.clear()
    async with factory() as s:
        await s.execute(sa.delete(User))
        users = {}
        for name, college, courses, policy, admin in [
            ("admin", None, [], None, True),
            ("lead", None, [], None, False),
            ("junior", None, [], None, False),
            ("writer", None, [], AUTHOR, False),
            ("reviewer", None, [], PUBLISHER, False),
            ("host", None, [], {"human_interviews": ["read"]}, False),
            ("nobody", None, [], None, False),
            ("asha", "City College", ["course-a"], None, False),
            ("bilal", "City College", ["course-a"], None, False),
            ("chitra", "Hill College", ["course-b"], None, False),
        ]:
            u = User(
                email=f"{name}@iv.test", name=name, password_hash="t", university=college,
                enrolled_course_ids=courses, enrollment_source="sampark", enrollment_observed_at=NOW,
                feature_permissions=policy, is_admin=admin,
            )
            s.add(u)
            await s.flush()
            users[name] = u.id
        team = Team(name="Programme")
        content = Team(name="Content")
        s.add_all([team, content])
        await s.flush()
        for member_team, name, role, colleges in [
            (team, "lead", "Lead", None), (team, "junior", "Jr Facilitator", ["City College"]),
            (content, "writer", "Team Member", None), (content, "reviewer", "Creator", None),
            (content, "nobody", "Team Member", None), (content, "host", "Team Member", None),
        ]:
            s.add(TeamMember(team_id=member_team.id, user_id=users[name], access_role=role, purposes=[] if role == "Team Member" else None, colleges=colleges))
        s.add(StudentPlacement(student_id=users["asha"], batch_key="a-1", batch_name="Batch A", course_id="course-a", course_name="Course A"))

        v1 = template("Data Analyst", "published", published=NOW - dt.timedelta(days=40), courses=["course-a"])
        s.add(v1)
        await s.flush()
        v2 = template("Data Analyst", "published", parent=v1.id, published=NOW - dt.timedelta(days=5), courses=["course-a"])
        draft_only = template("Business Analyst", "draft", per=2)
        mock = template("HR round", "published", mode="practice", published=NOW - dt.timedelta(days=9))
        s.add_all([v2, draft_only, mock])
        await s.flush()
        s.add_all(questions(v1.id) + questions(v2.id) + questions(draft_only.id))

        made = {}
        for label, who, profile, course, age, k, sp, rule, released in [
            ("held", "asha", v2.id, "course-a", 1, 70, 60, "review", None),
            ("shared", "bilal", v2.id, "course-a", 2, 80, 80, "immediate", None),
            ("old", "chitra", v1.id, "course-b", 20, 50, 50, "immediate", None),
            ("late", "asha", v2.id, "course-a", 1, None, None, "immediate", None),
            ("flagged", "bilal", v2.id, "course-a", 1, 60, 60, "review", None),
        ]:
            iv = Interview(
                user_id=users[who], track="Dev", course_id=course, interview_mode="assessment", status="completed",
                job_profile_id=profile, created_at=NOW - dt.timedelta(days=age), completed_at=NOW - dt.timedelta(days=age),
                duration_seconds=600, result_release=rule, result_released_at=released, difficulty="medium",
            )
            s.add(iv)
            await s.flush()
            made[label] = iv.id
            if k is not None:
                s.add(SummaryReport(interview_id=iv.id, report_json=report(k, sp)))
        practice = Interview(
            user_id=users["asha"], track="HR", course_id="course-a", interview_mode="practice", status="completed",
            job_profile_id=mock.id, created_at=NOW - dt.timedelta(days=1), completed_at=NOW - dt.timedelta(days=1),
        )
        s.add(practice)
        await s.flush()
        s.add(SummaryReport(interview_id=practice.id, report_json=report(90, 90)))
        s.add(IntegrityCase(
            interview_id=made["flagged"], student_id=users["bilal"], course_id="course-a", college="city college",
            opened_by=users["admin"], request_key=str(uuid.uuid4()), request_hash="x", state="open", history=[],
        ))
        for name, offset, status in [("asha", -2, "scheduled"), ("bilal", 3, "scheduled"), ("chitra", -5, "completed")]:
            row = HumanInterview(
                course_id="course-a", student_id=users[name], host_id=users["host"], title="Round 1", meet_url="https://meet.google.com/abc-defg-hij",
                starts_at=NOW + dt.timedelta(days=offset), ends_at=NOW + dt.timedelta(days=offset, minutes=30), request_key=str(uuid.uuid4()),
                request_hash="x", status=status, version=1, interviewer_id=users["host"],
            )
            s.add(row)
            await s.flush()
            made[f"human-{name}"] = row.id
        s.add(HumanScorecard(appointment_id=made["human-chitra"], criteria=[], status="submitted", version=1))
        s.add(NotificationDelivery(recipient_id=users["bilal"], event="scheduled", related_id=made["human-bilal"], status="failed"))
        opens, closes = NOW - dt.timedelta(days=1), NOW + dt.timedelta(days=2)
        for name, status in [("asha", "assigned"), ("bilal", "assigned")]:
            s.add(AssessmentAssignment(
                student_id=users[name], assigned_by=users["admin"], course_id="course-a", job_profile_id=v2.id,
                pinned_revision=0, difficulty="medium", opens_at=opens, closes_at=closes, request_key=str(uuid.uuid4()), status=status, version=1,
            ))
        await s.commit()
        made |= {"v1": v1.id, "v2": v2.id, "draft": draft_only.id, "mock": mock.id}
    app = fastapi.FastAPI()
    app.include_router(routes.router)

    async def session_dep():
        async with factory() as s:
            try:
                yield s
            finally:
                await s.rollback()

    async def actor(request: fastapi.Request, session=fastapi.Depends(get_async_session)):
        lh._cache.clear()
        return await session.get(User, users[request.headers["x-test-actor"]])

    app.dependency_overrides[get_async_session] = session_dep
    app.dependency_overrides[get_current_admin_user] = actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, users, made, factory


def as_(who):
    return {"x-test-actor": who}


async def get(c, path, who="admin", code=200, **params):
    r = await c.get(f"/v2/interview-hub/{path}", headers=as_(who), params=params)
    assert r.status_code == code, r.text
    return r.json()


async def post(c, path, who="admin", code=200, **body):
    r = await c.post(f"/v2/interview-hub/{path}", headers=as_(who), json=body)
    assert r.status_code == code, r.text
    return r.json()


async def test_each_kind_of_staff_gets_their_own_view(hc):
    c, users, made, _ = hc
    await get(c, "summary", "nobody", code=403)
    writer = await get(c, "summary", "writer")
    assert writer["can"]["author_only"] is True and writer["metrics"] is None
    assert writer["author"]["drafts"] == 1 and writer["author"]["live"] == 2
    assert not any(q["id"] == "held" for q in writer["queue"])
    await get(c, "attempts", "writer", code=403)
    host = await get(c, "summary", "host")
    assert host["can"]["host_only"] is True and host["metrics"] is None
    await get(c, "templates", "host", code=403)
    assert {x["student"] for x in (await get(c, "human", "host"))["items"]} == {"asha", "bilal", "chitra"}


async def test_college_scope_limits_attempts(hc):
    c, *_ = hc
    junior = await get(c, "attempts", "junior", days=30)
    assert {x["student"] for x in junior["items"]} == {"asha", "bilal"}
    everyone = await get(c, "attempts", days=30)
    assert "chitra" in {x["student"] for x in everyone["items"]}


async def test_status_tabs_add_up_and_filters_narrow(hc):
    c, users, made, _ = hc
    data = await get(c, "attempts", days=7)
    n = data["counts"]
    assert n["all"] == 4 and n["held"] + n["released"] + n["delayed"] + n["integrity"] == n["all"]
    assert (n["held"], n["released"], n["delayed"], n["integrity"]) == (1, 1, 1, 1)
    row = next(x for x in data["items"] if x["id"] == made["held"])
    assert (row["knowledge"], row["speech"], row["score"] is not None, row["batch"]) == (70, 60, True, "Batch A")
    assert (await get(c, "attempts", days=7, status="integrity"))["total"] == 1
    assert (await get(c, "attempts", days=7, batch="a-1"))["counts"]["all"] == 2
    assert (await get(c, "attempts", days=7, q=str(made["held"])))["total"] == 1
    assert (await get(c, "attempts", all=True, college="hill college"))["total"] == 1
    assert (await get(c, "attempts", of="practice", days=7))["total"] == 1
    await get(c, "attempts", days=9, code=422)


async def test_summary_agrees_with_learners_and_lists_work(hc):
    c, users, made, _ = hc
    data = await get(c, "summary", days=7)
    assert data["metrics"]["assessments"]["value"] == 4
    assert data["metrics"]["mock"]["value"] == 1
    ids = {q["id"] for q in data["queue"]}
    assert {"attendance", "review", "held", "integrity", "delayed", "invites"} <= ids
    assert any(i.startswith("closing-") for i in ids)
    ages = [q["oldest_days"] for q in data["queue"] if not q["id"].startswith("join-")]
    assert ages == sorted(ages, reverse=True)
    narrow = await get(c, "summary", days=7, college="hill college")
    assert narrow["metrics"]["assessments"]["value"] == 0


async def test_human_stages_and_invitation_resend(hc):
    c, users, made, factory = hc
    data = await get(c, "human")
    by = {x["student"]: x for x in data["items"]}
    assert by["asha"]["stage"] == "attendance" and by["bilal"]["stage"] == "upcoming" and by["chitra"]["stage"] == "review"
    assert by["bilal"]["invite_failed"] is True and data["counts"]["failed"] == 1
    assert (await get(c, "human", status="failed"))["total"] == 1
    await post(c, f"invitations/{by['bilal']['invite_delivery_id']}/resend")
    assert (await get(c, "human", status="failed"))["total"] == 0
    await post(c, f"invitations/{by['bilal']['invite_delivery_id']}/resend", code=409)


async def test_release_needs_reason_and_skips_integrity_cases(hc):
    c, users, made, factory = hc
    await post(c, "attempts/release", ids=[made["held"]], reason="short", code=422)
    done = await post(c, "attempts/release", ids=[made["held"], made["flagged"], made["shared"]], reason="Reviewed and accurate.")
    assert done["done"] == [made["held"]] and set(done["skipped"]) == {made["flagged"], made["shared"]}
    async with factory() as s:
        assert (await s.get(Interview, made["held"])).result_released_at is not None
        assert (await s.get(Interview, made["flagged"])).result_released_at is None
    await post(c, "attempts/release", "writer", ids=[made["held"]], reason="Reviewed and accurate.", code=403)


async def test_versions_collapse_into_one_interview(hc):
    c, users, made, _ = hc
    data = await get(c, "templates", days=30)
    names = [x["title"] for x in data["items"]]
    assert names.count("Data Analyst") == 1
    assert names[0] == "Business Analyst"  # not yet live comes first
    da = next(x for x in data["items"] if x["title"] == "Data Analyst")
    assert da["live"]["version"] == 2 and da["stats"]["finished"] == 5 and da["stats"]["held"] == 2
    detail = await get(c, f"templates/{made['v1']}")
    assert detail["id"] == made["v1"] and [v["version"] for v in detail["versions"]] == [2, 1]
    assert detail["versions"][0]["attempts"] == 4 and detail["versions"][1]["attempts"] == 1
    assert detail["results"]["held"] == 2 and detail["results"]["assignments"][0]["not_started"] == 2


async def test_editing_a_live_interview_makes_a_draft_and_publish_replaces_it(hc):
    c, users, made, factory = hc
    first = await post(c, f"templates/{made['v2']}/draft", "writer")
    assert first["created"] is True
    assert (await post(c, f"templates/{made['v2']}/draft", "writer"))["id"] == first["id"]
    await post(c, f"templates/{made['v2']}/publish", "writer", code=403)
    done = await post(c, f"templates/{made['v2']}/publish", "reviewer")
    assert done["id"] == first["id"]
    async with factory() as s:
        rows = (await s.execute(sa.select(JobProfile).where(JobProfile.job_name == "Data Analyst"))).scalars().all()
        assert sum(1 for r in rows if r.status == "published") == 3
    data = await get(c, f"templates/{made['v1']}")
    assert [v["version"] for v in data["versions"]] == [3, 2, 1] and data["live"]["version"] == 3
    assert data["versions"][1]["attempts"] == 4  # past attempts stay on the version they took


async def test_publish_needs_every_difficulty_and_discard_keeps_live(hc):
    c, users, made, factory = hc
    await post(c, f"templates/{made['draft']}/publish", code=409)
    async with factory() as s:
        s.add_all(questions(made["draft"]))
        await s.commit()
    async with factory() as s:
        extra = [JobProfileQuestion(job_profile_id=made["draft"], question_text=f"more {l}", level=l, difficulty="easy", question_type="technical") for l in (1, 2, 3, 4)]
        s.add_all(extra)
        await s.commit()
    assert (await post(c, f"templates/{made['draft']}/publish"))["status"] == "published"
    await post(c, f"templates/{made['v2']}/draft")
    r = await c.delete(f"/v2/interview-hub/templates/{made['v2']}/draft", headers=as_("admin"))
    assert r.status_code == 200
    assert (await get(c, f"templates/{made['v2']}"))["draft"] is None


async def test_review_loop(hc):
    c, users, made, factory = hc
    async with factory() as s:
        (await s.get(JobProfile, made["draft"])).status = "under_review"
        await s.commit()
    await post(c, f"templates/{made['draft']}/draft", "writer", code=409)
    await post(c, f"templates/{made['draft']}/request-changes", "writer", note="No", code=403)
    await post(c, f"templates/{made['draft']}/request-changes", "reviewer", note="Make Expert harder.")
    data = await get(c, f"templates/{made['draft']}", "writer")
    assert data["draft"]["status"] == "changes_requested" and data["draft"]["note"] == "Make Expert harder."
    assert data["results"] is None


async def test_assignment_group_remind_change_cancel(hc):
    c, users, made, factory = hc
    key = (await get(c, f"templates/{made['v2']}"))["results"]["assignments"][0]["key"]
    group = await get(c, f"assignments/{key}")
    assert group["total"] == 2 and group["not_started"] == 2 and group["phase"] == "open"
    assert (await post(c, f"assignments/{key}/remind"))["sent"] == 2
    again = await post(c, f"assignments/{key}/remind")
    assert again == {"sent": 0, "skipped": 2}
    assert all(m["reminded"] for m in (await get(c, f"assignments/{key}"))["members"])
    async with factory() as s:
        assert await s.scalar(sa.select(sa.func.count()).select_from(NotificationDelivery).where(NotificationDelivery.event == "nudge")) == 2
    new_close = (NOW + dt.timedelta(days=9)).isoformat()
    moved = await post(c, f"assignments/{key}/change", opens_at=(NOW - dt.timedelta(hours=1)).isoformat(), closes_at=new_close, reason="Exam week moved it.")
    assert moved["changed"] == 2
    await post(c, f"assignments/{key}/change", opens_at=(NOW - dt.timedelta(hours=1)).isoformat(), closes_at=new_close, reason="short", code=422)
    new = moved["key"]
    await get(c, f"assignments/{key}", code=404)
    assert (await post(c, f"assignments/{new}/cancel", reason="No longer needed here."))["cancelled"] == 2
    assert (await get(c, f"assignments/{new}"))["phase"] == "cancelled"
    await post(c, f"assignments/{new}/cancel", reason="No longer needed here.", code=409)


async def test_replaced_versions_are_hidden_from_learners(hc):
    c, users, made, factory = hc
    async with factory() as s:
        query = JobProfileCRUDRepository._listing_filters(
            sa.select(JobProfile), status="published", interview_mode="assessment", is_admin=False,
            category=None, search=None, visible_to=(["course-a"], []),
        )
        got = (await s.execute(query)).scalars().all()
    assert [p.id for p in got] == [made["v2"]]


async def test_attempt_detail_scope_and_questions(hc):
    c, users, made, factory = hc
    async with factory() as s:
        question = InterviewQuestion(interview_id=made["held"], text="Why this role?", category="behavioral", order=1, status="answered")
        s.add(question)
        await s.flush()
        s.add(QuestionAttempt(
            interview_id=made["held"], question_id=question.id, question_text="Why this role?", answer_kind="audio",
            answer_saved_at=NOW, transcription_status="transcribed", transcription={"text": "I like data."}, feedback="Add a result.",
        ))
        await s.commit()
    data = await get(c, f"attempts/{made['held']}/detail")
    assert (data["knowledge"], data["speech"]) == (70, 60) and data["delayed"] is False
    assert data["questions"][0]["answer"] == "I like data." and data["questions"][0]["feedback"] == "Add a result."
    await get(c, f"attempts/{made['old']}/detail", "junior", code=404)
    await get(c, f"attempts/{made['held']}/detail", "writer", code=403)


async def test_retry_report_restarts_stuck_answers_once(hc, monkeypatch):
    c, users, made, factory = hc
    from src.worker import queue

    jobs = []

    async def fake(name, **kwargs):
        jobs.append(name)
        return f"job-{len(jobs)}"

    monkeypatch.setattr(queue, "enqueue_job", fake)
    async with factory() as s:
        s.add(QuestionAttempt(
            interview_id=made["late"], question_text="Q", answer_kind="audio", answer_checksum="abc", answer_saved_at=NOW, audio_url="s3://x",
            transcription_status="failed", transcription_updated_at=NOW - dt.timedelta(hours=1),
        ))
        await s.commit()
    await post(c, f"attempts/{made['late']}/retry-report", "lead", code=403)
    await post(c, f"attempts/{made['shared']}/retry-report", code=409)
    done = await post(c, f"attempts/{made['late']}/retry-report")
    assert done == {"state": "queued", "answers_restarted": 1}
    assert jobs == ["process_answer_transcription_task", "generate_summary_report_task"]
    assert (await get(c, f"attempts/{made['late']}/detail"))["retry_requested_at"]
    await post(c, f"attempts/{made['late']}/retry-report", code=409)
    assert len(jobs) == 2


async def test_retry_report_says_so_when_queue_is_down(hc, monkeypatch):
    c, users, made, _ = hc
    from src.worker import queue

    async def down(name, **kwargs):
        return None

    monkeypatch.setattr(queue, "enqueue_job", down)
    await post(c, f"attempts/{made['late']}/retry-report", code=503)
    assert (await get(c, f"attempts/{made['late']}/detail"))["retry_requested_at"] is None


async def test_integrity_list_across_courses(hc):
    c, users, made, factory = hc
    await get(c, "integrity", "writer", code=403)
    data = await get(c, "integrity")
    assert data["counts"] == {"open": 1, "closed": 0} and data["items"][0]["student"] == "bilal"
    assert data["items"][0]["can_decide"] is True
    case = data["items"][0]["id"]
    assert (await get(c, "integrity", case=case))["total"] == 1
    assert (await get(c, "integrity", state="closed"))["total"] == 0
