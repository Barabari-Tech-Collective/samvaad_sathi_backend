"""Real PostgreSQL vertical slice; only runs against an explicitly isolated DB."""

import asyncio
import datetime
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fastapi
import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import src.models.db  # noqa: F401
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.admin import get_current_creator_user
from src.api.dependencies.session import get_async_session
from src.api.routes import audio, interviews, interviews_v2, job_profiles_v2
from src.models.db.interview import Interview
from src.models.db.job_profile_question import JobProfileQuestion
from src.models.db.user import User
from src.repository.crud.interview_question import InterviewQuestionCRUDRepository
from src.repository.crud.question import QuestionAttemptCRUDRepository
from src.models.db.question_attempt import QuestionAttempt
from src.repository.table import Base

URL = os.environ.get("FIXED_ROUND_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not URL, reason="Requires an explicitly isolated local PostgreSQL test database"
)


@pytest.fixture
async def db():
    target = sa.engine.make_url(URL)
    if (
        target.host not in ("127.0.0.1", "localhost")
        or target.database != "ss_count_tests"
    ):
        raise RuntimeError(
            "Refusing schema mutation outside the isolated ss_count_tests database"
        )
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        actor = User(
            email="round@example.test", name="Round Student", password_hash="test-only"
        )
        session.add(actor)
        await session.commit()
        actor_id = actor.id
    yield engine, factory, actor_id
    await engine.dispose()


@pytest.fixture
async def client(db, monkeypatch):
    _, factory, actor_id = db
    app = fastapi.FastAPI()
    for router in (
        interviews.router,
        interviews_v2.router,
        job_profiles_v2.router,
        audio.router,
    ):
        app.include_router(router)

    async def session():
        async with factory() as value:
            try:
                yield value
            finally:
                await value.rollback()

    actor = SimpleNamespace(
        is_admin=True,
        id=actor_id,
        enrolled_course_ids=[],
        resume_text=None,
        years_experience=None,
        skills={},
    )
    app.dependency_overrides[get_async_session] = session
    app.dependency_overrides[get_current_user] = lambda: actor
    app.dependency_overrides[get_current_creator_user] = lambda: actor
    # No production analytics session, external model, storage or TTS calls.
    for module in (interviews, interviews_v2):
        monkeypatch.setattr(module, "track_analytics_event", AsyncMock())
    monkeypatch.setattr(interviews, "_get_supplement_map", AsyncMock(return_value={}))
    monkeypatch.setattr(
        interviews_v2, "_get_supplement_map", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        audio,
        "upload_answer_audio_to_s3",
        AsyncMock(return_value="Samvaad-Saathi/answer-audio/fixture.wav"),
    )
    monkeypatch.setattr(
        audio,
        "validate_audio_file",
        AsyncMock(
            return_value=(
                b"fixture-audio",
                {"filename": "answer.wav", "content_type": "audio/wav", "size": 13},
            )
        ),
    )
    monkeypatch.setattr(audio, "get_audio_duration_estimate", lambda *_args: 2.0)
    monkeypatch.setattr(audio, "enqueue_job", AsyncMock(return_value="isolated-job"))
    monkeypatch.setattr(interviews_v2.settings, "MOCK_TTS", True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as value:
        yield value


async def published_role(client, db, total=8, purpose="assessment", bank=12):
    response = await client.post(
        "/v2/assessment-roles" if purpose == "assessment" else "/v2/job-profiles",
        json={
            "jobName": "Round Role",
            "interviewMode": purpose,
            "questionsPerInterview": total,
        },
    )
    assert response.status_code == 201, response.text
    role_id = response.json()["id"]
    _, factory, _ = db
    async with factory() as session:
        session.add_all(
            [
                JobProfileQuestion(
                    job_profile_id=role_id,
                    question_text=f"Explain scenario {i}",
                    level=1,
                    difficulty="easy",
                    expected_answer=f"PRIVATE RUBRIC {i}",
                )
                for i in range(bank)
            ]
        )
        await session.commit()
    response = await client.patch(
        (
            f"/v2/assessment-roles/{role_id}/review"
            if purpose == "assessment"
            else f"/v2/job-profiles/{role_id}/review"
        ),
        json={"status": "published", "adminComment": "Approved for testing"},
    )
    assert response.status_code == 200, response.text
    return role_id


async def prepare(client, role_id):
    response = await client.post(
        "/v2/interviews/create",
        json={
            "clientContract": "fixed-round-v1",
            "track": "Round Role",
            "difficulty": "easy",
            "jobProfileId": role_id,
        },
    )
    assert response.status_code == 201, response.text
    sitting = response.json()
    response = await client.post(
        "/v2/interviews/generate-questions",
        json={"interviewId": sitting["interviewId"], "useResume": False},
    )
    assert response.status_code == 201, response.text
    return sitting, response.json()


async def test_creator_to_eight_question_assessment_receipt_and_resume(client, db):
    role_id = await published_role(client, db)
    sitting, generated = await prepare(client, role_id)
    interview_id = sitting["interviewId"]
    assert sitting["plannedQuestionCount"] == generated["count"] == 8
    assert generated["interviewMode"] == "assessment"
    assert "PRIVATE RUBRIC" not in str(generated)
    assert all(q["followUpStrategy"] is None for q in generated["items"])
    start = await client.post("/interviews/begin", json={"interviewId": interview_id})
    assert start.status_code == 200, start.text
    duplicate = await client.post(
        "/interviews/begin", json={"interviewId": interview_id}
    )
    assert duplicate.json()["answerDeadlineAt"] == start.json()["answerDeadlineAt"]
    checkpoint = (
        await client.post("/interviews/resume", json={"interviewId": interview_id})
    ).json()
    for index in range(8):
        action = "skip"
        answer_version = {}
        if index == 0:
            attempt = await client.post(
                "/interviews/question-attempts",
                json={
                    "interviewId": interview_id,
                    "questionId": checkpoint["currentQuestionId"],
                    "clientAttemptId": "fixed-voice-answer-1",
                },
            )
            assert attempt.status_code == 201, attempt.text
            saved = await client.post(
                "/transcribe-whisper",
                data={"question_attempt_id": str(attempt.json()["questionAttemptId"])},
                files={"file": ("answer.wav", b"fixture-audio", "audio/wav")},
            )
            assert saved.status_code == 202, saved.text
            assert saved.json()["saved"] is True
            action = "answer_saved"
            answer_version = {"answerVersion": saved.json()["answerVersion"]}
        response = await client.post(
            "/interviews/navigation/advance",
            json={
                "interviewId": interview_id,
                "fromQuestionId": checkpoint["currentQuestionId"],
                "expectedStateVersion": checkpoint["stateVersion"],
                "transitionId": f"advance-fixed-{index}",
                "action": action,
                **answer_version,
            },
        )
        assert response.status_code == 200, response.text
        checkpoint = response.json()
        assert len(checkpoint["questions"]) == 8
    assert checkpoint["completeReady"] is True
    response = await client.post(
        "/interviews/complete", json={"interviewId": interview_id}
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["receipt"]
    assert (
        receipt["planned"],
        receipt["answered"],
        receipt["skipped"],
        receipt["unreached"],
    ) == (8, 1, 7, 0)
    repeated = await client.post(
        "/interviews/complete", json={"interviewId": interview_id}
    )
    assert repeated.json()["receipt"] == receipt
    assert (await client.get(f"/interviews/{interview_id}/receipt")).json()[
        "receipt"
    ] == receipt


async def test_three_question_practice_is_not_five_and_bank_changes_do_not_rewrite_it(
    client, db
):
    role_id = await published_role(client, db, total=3, purpose="practice", bank=3)
    # Start first to freeze approved source, then mutate the source bank.
    started = await client.post(
        "/v2/interviews/create",
        json={
            "clientContract": "fixed-round-v1",
            "track": "Round Role",
            "difficulty": "easy",
            "jobProfileId": role_id,
        },
    )
    assert started.status_code == 201, started.text
    _, factory, _ = db
    async with factory() as session:
        await session.execute(
            sa.update(JobProfileQuestion)
            .where(JobProfileQuestion.job_profile_id == role_id)
            .values(question_text="Changed bank content")
        )
        await session.commit()
    response = await client.post(
        "/v2/interviews/generate-questions",
        json={"interviewId": started.json()["interviewId"], "useResume": False},
    )
    assert response.status_code == 201, response.text
    assert response.json()["count"] == 3
    assert response.json()["questions"] == [f"Explain scenario {i}" for i in range(3)]


async def test_short_bank_and_student_override_are_rejected(client, db):
    response = await client.post(
        "/v2/assessment-roles", json={"jobName": "Short", "questionsPerInterview": 8}
    )
    role_id = response.json()["id"]
    response = await client.patch(
        f"/v2/assessment-roles/{role_id}/review", json={"status": "published"}
    )
    assert response.status_code == 409
    response = await client.post(
        "/v2/interviews/create",
        json={
            "clientContract": "fixed-round-v1",
            "track": "Short",
            "jobProfileId": role_id,
            "questionCount": 1,
        },
    )
    assert response.status_code == 422


async def test_concurrent_generation_and_followup_cannot_grow_fixed_round(client, db):
    role_id = await published_role(client, db)
    created = await client.post(
        "/v2/interviews/create",
        json={
            "clientContract": "fixed-round-v1",
            "track": "Round Role",
            "difficulty": "easy",
            "jobProfileId": role_id,
        },
    )
    interview_id = created.json()["interviewId"]
    replies = await asyncio.gather(
        *[
            client.post(
                "/v2/interviews/generate-questions",
                json={"interviewId": interview_id, "useResume": False},
            )
            for _ in range(2)
        ]
    )
    assert all(response.status_code == 201 for response in replies), [
        r.text for r in replies
    ]
    assert replies[0].json()["questionIds"] == replies[1].json()["questionIds"]
    _, factory, _ = db
    async with factory() as session:
        repo = InterviewQuestionCRUDRepository(session)
        with pytest.raises(ValueError, match="no additional"):
            await repo.create_follow_up_question(
                interview_id=interview_id,
                parent_question_id=replies[0].json()["questionIds"][0],
                text="Ninth question",
            )
        await session.rollback()
        assert len(await repo.list_by_interview(interview_id=interview_id)) == 8


async def test_deadline_receipt_does_not_claim_all_questions_answered(client, db):
    role_id = await published_role(client, db)
    sitting, _ = await prepare(client, role_id)
    interview_id = sitting["interviewId"]
    await client.post("/interviews/begin", json={"interviewId": interview_id})
    _, factory, _ = db
    async with factory() as session:
        await session.execute(
            sa.update(Interview)
            .where(Interview.id == interview_id)
            .values(
                answer_deadline_at=datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(seconds=90)
            )
        )
        await session.commit()
    response = await client.post(
        "/interviews/complete", json={"interviewId": interview_id}
    )
    assert response.status_code == 200, response.text
    assert response.json()["receipt"]["reason"] == "time_limit"
    assert response.json()["receipt"]["answered"] == 0
    assert response.json()["receipt"]["unreached"] == 8


async def test_competing_next_requests_advance_only_one_slot(client, db):
    role_id = await published_role(client, db)
    sitting, _ = await prepare(client, role_id)
    interview_id = sitting["interviewId"]
    await client.post("/interviews/begin", json={"interviewId": interview_id})
    checkpoint = (
        await client.post("/interviews/resume", json={"interviewId": interview_id})
    ).json()
    payload = {
        "interviewId": interview_id,
        "fromQuestionId": checkpoint["currentQuestionId"],
        "expectedStateVersion": checkpoint["stateVersion"],
        "action": "skip",
    }
    results = await asyncio.gather(
        *[
            client.post(
                "/interviews/navigation/advance",
                json={**payload, "transitionId": f"competing-next-{i}"},
            )
            for i in range(2)
        ]
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    refreshed = (
        await client.post("/interviews/resume", json={"interviewId": interview_id})
    ).json()
    assert refreshed["currentQuestionIndex"] == 1
    assert refreshed["plannedQuestionCount"] == 8
    premature = await client.post(
        "/interviews/complete", json={"interviewId": interview_id}
    )
    assert premature.status_code == 409


async def test_published_count_cannot_be_edited_under_active_round(client, db):
    role_id = await published_role(client, db)
    sitting, _ = await prepare(client, role_id)
    response = await client.put(
        f"/v2/assessment-roles/{role_id}", json={"questionsPerInterview": 12}
    )
    assert response.status_code == 409
    refreshed = await client.post(
        "/interviews/resume", json={"interviewId": sitting["interviewId"]}
    )
    assert refreshed.json()["plannedQuestionCount"] == 8


async def test_free_practice_preparation_rejects_short_output_then_retries_same_plan(
    client, db, monkeypatch
):
    created = await client.post(
        "/v2/interviews/create",
        json={
            "clientContract": "fixed-round-v1",
            "track": "General interview",
            "difficulty": "easy",
            "questionCount": 3,
        },
    )
    assert created.status_code == 201, created.text
    interview_id = created.json()["interviewId"]
    assert (
        await client.post("/interviews/begin", json={"interviewId": interview_id})
    ).status_code == 409
    generator = AsyncMock(
        return_value=(["Only one"], None, 0, "fixture", [{"text": "Only one"}])
    )
    monkeypatch.setattr(
        interviews_v2, "generate_full_stack_questions_with_llm", generator
    )
    short = await client.post(
        "/v2/interviews/generate-questions",
        json={"interviewId": interview_id, "useResume": False},
    )
    assert short.status_code == 503
    assert generator.await_args.kwargs["count"] == 3
    items = [{"text": f"Practice prompt {i}"} for i in range(3)]
    generator.return_value = ([q["text"] for q in items], None, 0, "fixture", items)
    retried = await client.post(
        "/v2/interviews/generate-questions",
        json={"interviewId": interview_id, "useResume": False},
    )
    assert retried.status_code == 201, retried.text
    assert retried.json()["count"] == retried.json()["plannedQuestionCount"] == 3


async def test_additive_migration_preserves_old_attempts_as_legacy(db):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine, _, actor_id = db
    path = (
        Path(__file__).resolve().parents[2]
        / "src/repository/migrations/versions/2026_10_19_0100-e7a8b9c0d1e2_add_fixed_question_plans.py"
    )
    spec = importlib.util.spec_from_file_location("fixed_question_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with engine.begin() as conn:
        for column in (
            "question_plan",
            "started_at",
            "answer_deadline_at",
            "submission_receipt",
        ):
            await conn.execute(sa.text(f"ALTER TABLE interview DROP COLUMN {column}"))
        await conn.execute(
            sa.text("ALTER TABLE job_profile DROP COLUMN questions_per_interview")
        )
        interview_id = (
            await conn.execute(
                sa.text(
                    "INSERT INTO interview (user_id, track, difficulty, status) VALUES (:user, 'Legacy', 'easy', 'active') RETURNING id"
                ),
                {"user": actor_id},
            )
        ).scalar_one()

        def upgrade(sync_conn):
            with Operations.context(MigrationContext.configure(sync_conn)):
                migration.upgrade()

        await conn.run_sync(upgrade)
        record = (
            await conn.execute(
                sa.text(
                    "SELECT question_plan, interview_mode FROM interview WHERE id=:id"
                ),
                {"id": interview_id},
            )
        ).one()
        assert record.question_plan is None
        assert record.interview_mode == "practice"


@pytest.mark.parametrize("total", [1, 50])
async def test_curated_count_boundaries_are_delivered_exactly(client, db, total):
    role_id = await published_role(client, db, total=total, bank=total)
    _, generated = await prepare(client, role_id)
    assert generated["count"] == total
    assert len(set(generated["questionIds"])) == total


async def test_competing_formal_audio_saves_cannot_replace_a_committed_answer(
    client, db
):
    role_id = await published_role(client, db)
    sitting, generated = await prepare(client, role_id)
    interview_id = sitting["interviewId"]
    await client.post("/interviews/begin", json={"interviewId": interview_id})
    attempt = await client.post(
        "/interviews/question-attempts",
        json={
            "interviewId": interview_id,
            "questionId": generated["questionIds"][0],
            "clientAttemptId": "immutable-audio-test",
        },
    )
    assert attempt.status_code == 201, attempt.text
    attempt_id = attempt.json()["questionAttemptId"]
    _, factory, _ = db

    async def save(label):
        async with factory() as session:
            try:
                return await QuestionAttemptCRUDRepository(session).save_audio_asset(
                    question_attempt_id=attempt_id,
                    audio_url=f"private/{label}",
                    checksum=label,
                    immutable=True,
                    follow_up_expected=False,
                )
            finally:
                await session.rollback()

    results = await asyncio.gather(
        save("first"), save("second"), return_exceptions=True
    )
    assert sum(isinstance(result, ValueError) for result in results) == 1
    async with factory() as session:
        stored = await session.get(QuestionAttempt, attempt_id)
        assert stored.answer_version == 1
        assert stored.follow_up_disposition == "not_applicable"


async def test_non_tech_uses_same_fixed_approved_snapshot(client, db):
    role_id = await published_role(client, db, total=3, purpose="practice", bank=3)
    response = await client.post(
        "/v2/interviews/non-tech/generate-questions",
        json={
            "clientContract": "fixed-round-v1",
            "jobProfileId": role_id,
            "difficulty": "easy",
            "useResume": True,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["plannedQuestionCount"] == response.json()["count"] == 3
    assert response.json()["questions"] == [f"Explain scenario {i}" for i in range(3)]


async def test_old_client_is_blocked_from_a_new_plan_and_receipt_is_private(client, db):
    role_id = await published_role(client, db)
    response = await client.post(
        "/v2/interviews/create",
        json={"track": "Round Role", "difficulty": "easy", "jobProfileId": role_id},
    )
    assert response.status_code == 409
    sitting, _ = await prepare(client, role_id)
    # A different authenticated identity cannot discover this user's receipt.
    from src.api.dependencies.auth import get_current_user

    app = client._transport.app
    app.dependency_overrides[get_current_user] = lambda: ItemUser(db[2] + 1000)
    response = await client.get(f"/interviews/{sitting['interviewId']}/receipt")
    assert response.status_code == 404


def ItemUser(user_id):
    return SimpleNamespace(id=user_id)


async def test_legacy_active_attempt_is_not_retrofitted_but_next_sitting_is_pinned(
    client, db
):
    from src.models.db.job_profile import JobProfile
    from src.repository.crud.interview import InterviewCRUDRepository

    role_id = await published_role(client, db)
    _, factory, user_id = db
    async with factory() as session:
        await session.execute(
            sa.update(JobProfile)
            .where(JobProfile.id == role_id)
            .values(questions_per_interview=None)
        )
        await session.commit()
        repo = InterviewCRUDRepository(session)
        legacy = await repo.create_interview(
            user_id=user_id,
            track="Round Role",
            difficulty="easy",
            job_profile_id=role_id,
        )
        assert legacy.question_plan is None
        legacy_id = legacy.id
    resumed = await client.post(
        "/v2/interviews/create",
        json={
            "clientContract": "fixed-round-v1",
            "track": "Round Role",
            "difficulty": "easy",
            "jobProfileId": role_id,
        },
    )
    assert resumed.status_code == 201
    assert resumed.json()["interviewId"] == legacy_id
    assert resumed.json()["plannedQuestionCount"] is None
    async with factory() as session:
        await session.execute(
            sa.update(Interview)
            .where(Interview.id == legacy_id)
            .values(status="completed")
        )
        await session.commit()
    _, generated = await prepare(client, role_id)
    assert generated["plannedQuestionCount"] == generated["count"] == 5


async def test_practice_creator_bank_review_publish_and_student_plan(client, db):
    response = await client.post(
        "/v2/practice-templates",
        json={"jobName": "Clear projects", "questionsPerInterview": 3},
    )
    assert response.status_code == 201, response.text
    template = response.json()["id"]
    listed = await client.get("/v2/practice-templates")
    assert any(
        row["id"] == template and row["status"] == "draft"
        for row in listed.json()["items"]
    )
    assert (
        await client.patch(
            f"/v2/practice-templates/{template}/review", json={"status": "published"}
        )
    ).status_code == 409
    assert (
        await client.post(f"/v2/practice-templates/{template}/submit")
    ).status_code == 409
    for i in range(3):
        added = await client.post(
            f"/v2/practice-templates/{template}/questions",
            json={
                "question": f"Explain project choice {i}",
                "level": 1,
                "difficulty": "easy",
                "expected_answer": "PRIVATE guidance",
            },
        )
        assert added.status_code == 201, added.text
    duplicate = await client.post(
        f"/v2/practice-templates/{template}/questions",
        json={
            "question": "  EXPLAIN   project choice 0 ",
            "level": 1,
            "difficulty": "easy",
        },
    )
    assert duplicate.status_code == 409
    submitted = await client.post(f"/v2/practice-templates/{template}/submit")
    assert submitted.status_code == 200, submitted.text
    assert (
        await client.post(
            f"/v2/practice-templates/{template}/questions",
            json={"question": "late mutation", "level": 1, "difficulty": "easy"},
        )
    ).status_code == 201
    assert (await client.post(f"/v2/practice-templates/{template}/submit")).status_code == 200
    published = await client.patch(
        f"/v2/practice-templates/{template}/review",
        json={"status": "published", "adminComment": "Reviewed"},
    )
    assert published.status_code == 200, published.text
    assert published.json()["interviewMode"] == "practice"
    assert (
        await client.put(
            f"/v2/assessment-roles/{template}", json={"interviewMode": "assessment"}
        )
    ).status_code == 409
    created = await client.post(
        "/v2/interviews/create",
        json={
            "jobProfileId": template,
            "track": "Clear projects",
            "difficulty": "easy",
            "clientContract": "fixed-round-v1",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["interviewMode"] == "practice"
    assert created.json()["plannedQuestionCount"] == 3


async def test_practice_question_delete_checks_parent_and_review_freeze(client, db):
    a = (
        await client.post(
            "/v2/practice-templates",
            json={"jobName": "Practice A", "questionsPerInterview": 1},
        )
    ).json()["id"]
    b = (
        await client.post(
            "/v2/practice-templates",
            json={"jobName": "Practice B", "questionsPerInterview": 1},
        )
    ).json()["id"]
    q = await client.post(
        f"/v2/practice-templates/{a}/questions",
        json={"question": "Tell me about a project", "level": 1, "difficulty": "easy"},
    )
    qid = q.json()["questionId"]
    assert (
        await client.delete(f"/v2/practice-templates/{b}/questions/{qid}")
    ).status_code == 404
    assert (await client.post(f"/v2/practice-templates/{a}/submit")).status_code == 200
    assert (
        await client.patch(
            f"/v2/practice-templates/{a}/review", json={"status": "changes_requested"}
        )
    ).status_code == 422
    assert (
        await client.patch(
            f"/v2/practice-templates/{a}/review",
            json={"status": "changes_requested", "adminComment": "Improve the prompt"},
        )
    ).status_code == 200
    assert (
        await client.delete(f"/v2/practice-templates/{a}/questions/{qid}")
    ).status_code == 200
    assert (await client.post(f"/v2/practice-templates/{a}/submit")).status_code == 409


@pytest.mark.parametrize(
    "path,mode",
    [("practice-templates", "assessment"), ("assessment-roles", "practice")],
)
async def test_cross_purpose_payload_is_rejected(client, path, mode):
    assert (
        await client.post(
            f"/v2/{path}", json={"jobName": "Wrong purpose", "interviewMode": mode}
        )
    ).status_code == 422


async def test_integrity_signal_replay_and_concurrency_preserve_attempt(client, db):
    role = await published_role(client, db, total=1, bank=1)
    sitting, _questions = await prepare(client, role)
    interview_id = sitting["interviewId"]
    started = await client.post("/interviews/begin", json={"interviewId": interview_id})
    assert started.status_code == 200, started.text
    _, factory, actor_id = db
    from src.repository.crud.interview import InterviewCRUDRepository

    async def send(event_id, reason):
        async with factory() as session:
            return await InterviewCRUDRepository(session).record_integrity_event(
                interview_id=interview_id,
                user_id=actor_id,
                reason=reason,
                event_id=event_id,
            )

    async with factory() as session:
        before = await session.get(Interview, interview_id)
        assert before.started_at is not None and before.answer_deadline_at is not None
        expected = (
            before.started_at,
            before.answer_deadline_at,
            before.current_question_id,
            before.state_version,
            before.question_plan,
        )
    await asyncio.gather(
        send("same", "tab_switch"),
        send("same", "tab_switch"),
        send("different", "fullscreen_exit"),
    )
    async with factory() as session:
        row = await session.get(Interview, interview_id)
        assert row.status == "active" and row.proctoring_terminated is False
        assert len(row.integrity_events) == 2
        assert all(e["verdict"] is None for e in row.integrity_events)
        original = (
            row.started_at,
            row.answer_deadline_at,
            row.current_question_id,
            row.state_version,
            row.question_plan,
        )
    assert original == expected
    response = await client.post(
        "/interviews/proctoring-violation",
        json={"interviewId": interview_id, "eventId": "same", "reason": "tab_switch"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["eventCount"] == 2 and response.json()["status"] == "active"
    async with factory() as session:
        row = await session.get(Interview, interview_id)
        assert original == (
            row.started_at,
            row.answer_deadline_at,
            row.current_question_id,
            row.state_version,
            row.question_plan,
        )


async def test_integrity_journal_is_bounded_and_owner_checked(client, db):
    _, factory, actor_id = db
    async with factory() as session:
        row = Interview(
            user_id=actor_id,
            track="Backend",
            difficulty="easy",
            status="active",
            interview_mode="assessment",
            integrity_events=[
                {"eventId": str(i), "reason": "tab_switch"} for i in range(100)
            ],
        )
        session.add(row)
        await session.commit()
        interview_id = row.id
    response = await client.post(
        "/interviews/proctoring-violation",
        json={
            "interviewId": interview_id,
            "eventId": "overflow",
            "reason": "tab_switch",
        },
    )
    assert response.status_code == 200, response.text
    assert (
        response.json()["eventCount"] == 100
        and response.json()["proctoringTerminated"] is False
    )
    from src.repository.crud.interview import InterviewCRUDRepository

    async with factory() as session:
        with pytest.raises(fastapi.HTTPException) as error:
            await InterviewCRUDRepository(session).record_integrity_event(
                interview_id=interview_id,
                user_id=actor_id + 100,
                reason="tab_switch",
                event_id="other-user",
            )
        assert error.value.status_code == 404


async def test_integrity_event_migration_preserves_existing_interview(db):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine, factory, user_id = db
    async with factory() as session:
        row = Interview(
            user_id=user_id,
            track="Legacy",
            difficulty="easy",
            status="active",
            interview_mode="assessment",
        )
        session.add(row)
        await session.commit()
        row_id = row.id
    path = (
        Path(__file__).parents[2]
        / "src/repository/migrations/versions/2026_10_19_0200-f8b9c0d1e2f3_add_integrity_events.py"
    )
    spec = importlib.util.spec_from_file_location("integrity_event_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with engine.begin() as connection:
        await connection.execute(
            sa.text("ALTER TABLE interview DROP COLUMN integrity_events")
        )

        def upgrade(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.upgrade()

        await connection.run_sync(upgrade)
        result = (
            await connection.execute(
                sa.text(
                    "SELECT status, interview_mode, integrity_events FROM interview WHERE id=:id"
                ),
                {"id": row_id},
            )
        ).one()
        assert tuple(result) == ("active", "assessment", None)


async def authoring_draft(client, purpose="practice", method="ai"):
    prefix = "practice-templates" if purpose == "practice" else "assessment-roles"
    created = await client.post(f"/v2/{prefix}", json={"jobName": "Authored round", "jobDescription": "Explain choices clearly and support them with examples.", "interviewMode": purpose, "authoringMode": method, "questionsPerInterview": 1})
    assert created.status_code == 201, created.text
    return prefix, created.json()["id"]


@pytest.mark.parametrize("purpose,method", [("practice","ai"),("practice","manual"),("assessment","ai"),("assessment","manual")])
async def test_ai_and_manual_authoring_stay_editable_until_publication(client, db, purpose, method):
    prefix, profile_id = await authoring_draft(client, purpose, method)
    added = await client.post(f"/v2/job-profiles/{profile_id}/questions", json={"question":"Explain your project", "level":1,"difficulty":"easy"})
    assert added.status_code == 201, added.text
    qid = added.json()["questionId"]
    assert (await client.post(f"/v2/{prefix}/{profile_id}/submit")).status_code == 200
    edited = await client.patch(f"/v2/job-profile-questions/{qid}", json={"question":"Explain your most difficult project choice", "expectedAnswer":"PRIVATE support with trade-offs", "keywords":["trade-off"],"conceptsCovered":["reasoning"],"exampleOutput":"Example explanation"})
    assert edited.status_code == 200, edited.text
    listing = (await client.get(f"/v2/{prefix}")).json()["items"]
    profile = next(p for p in listing if p["id"] == profile_id)
    assert profile["status"] == "draft"  # editing invalidates a pending review
    assert profile["authoringMode"] == method
    # A reviewer holding the earlier draft cannot approve unseen changes.
    stale = await client.patch(f"/v2/{prefix}/{profile_id}/review", json={"status":"published","expectedRevision":0})
    assert stale.status_code == 409
    assert (await client.post(f"/v2/{prefix}/{profile_id}/submit")).status_code == 200
    approved = await client.patch(f"/v2/{prefix}/{profile_id}/review", json={"status":"published","expectedRevision":profile["contentRevision"],"adminComment":"Reviewed"})
    assert approved.status_code == 200, approved.text
    from src.models.db.job_profile import JobProfile
    async with db[1]() as session:
        stored = await session.get(JobProfile, profile_id)
        assert stored.published_at is not None
        assert stored.publication_snapshot["questions"][0]["expected_answer"] == "PRIVATE support with trade-offs"
    clone = await client.post(f"/v2/{prefix}/{profile_id}/clone")
    assert clone.status_code == 201, clone.text
    assert clone.json()["parentProfileId"] == profile_id and clone.json()["status"] == "draft"
    new_id = clone.json()["id"]
    assert (await client.put(f"/v2/{prefix}/{new_id}",json={"jobName":"Revised next round","interviewMode":purpose})).status_code == 200
    assert (await client.put(f"/v2/{prefix}/{profile_id}",json={"jobName":"Overwrite publication","interviewMode":purpose})).status_code == 409


@pytest.mark.parametrize("operation", ["metadata","question_edit","question_add","question_delete","delete_all","profile_delete","regenerate","generate","resubmit","review_reopen"])
async def test_every_legacy_writer_rejects_published_content(client, db, operation):
    profile_id = await published_role(client, db, total=1, bank=1)
    questions = (await client.get(f"/v2/job-profiles/{profile_id}/questions")).json()["questions"]
    qid = questions[0]["questionId"]
    requests = {
        "metadata": ("put",f"/v2/job-profiles/{profile_id}",{"jobName":"Changed"}),
        "question_edit":("patch",f"/v2/job-profile-questions/{qid}",{"question":"Changed"}),
        "question_add":("post",f"/v2/job-profiles/{profile_id}/questions",{"question":"Changed", "level":1,"difficulty":"easy"}),
        "question_delete":("delete",f"/v2/job-profile-questions/{qid}",None),
        "delete_all":("delete",f"/v2/job-profiles/{profile_id}/questions",None),
        "profile_delete":("delete",f"/v2/job-profiles/{profile_id}",None),
        "regenerate":("post",f"/v2/job-profile-questions/{qid}/regenerate",None),
        "generate":("post",f"/v2/job-profiles/{profile_id}/questions/generate",{"levels":[{"level":1,"count":1}]}),
        "resubmit":("post",f"/v2/job-profiles/{profile_id}/submit",None),
        "review_reopen":("patch",f"/v2/job-profiles/{profile_id}/review",{"status":"changes_requested","adminComment":"Reopen"}),
    }
    method,path,body=requests[operation]
    response = await getattr(client,method)(path,**({"json":body} if body is not None else {}))
    assert response.status_code == 409, (operation,response.text)


async def test_ai_queue_appends_to_manual_bank_and_is_bound_to_profile(client, db, monkeypatch):
    from src.repository.database import async_db
    from src.worker.tasks import generate_questions_task
    from src.services import question_generation
    from src.models.db.job_profile import JobProfile
    _, factory, _ = db
    monkeypatch.setattr(async_db, "get_session", lambda: factory())
    async def enqueue(_name, **kwargs):
        return kwargs["_job_id"]
    monkeypatch.setattr(job_profiles_v2, "enqueue_job", enqueue)
    async def generate(**kwargs):
        return [(1,"easy",{"text":f"AI generated prompt {i}","expected_answer":"Editable AI rubric"}) for i in range(kwargs["count"])]
    monkeypatch.setattr(question_generation, "generate_questions_for_level", generate)
    _, profile_id = await authoring_draft(client, method="manual")
    assert (await client.post(f"/v2/job-profiles/{profile_id}/questions",json={"question":"Keep my manual prompt","level":1,"difficulty":"easy"})).status_code == 201
    response = await client.post(f"/v2/job-profiles/{profile_id}/questions/generate", json={"levels":[{"level":1,"count":2}]})
    assert response.status_code == 202, response.text
    token = response.json()["jobId"]
    assert (await client.post(f"/v2/job-profiles/{profile_id}/questions/generate",json={"levels":[{"level":1,"count":1}]})).status_code == 409
    other = (await authoring_draft(client))[1]
    assert (await client.get(f"/v2/job-profiles/{other}/questions/generate/status/{token}")).status_code == 404
    async with factory() as session:
        revision = (await session.get(JobProfile,profile_id)).content_revision
    result = await generate_questions_task({},job_profile_id=profile_id,levels=[{"level":1,"count":2}],generation_token=token,expected_revision=revision)
    assert result["count"] == 2
    bank = (await client.get(f"/v2/job-profiles/{profile_id}/questions")).json()["questions"]
    assert len(bank)==3 and bank[0]["question"] == "Keep my manual prompt"
    assert bank[0]["isAiGenerated"] is False and bank[1]["isAiGenerated"] is True
    result = (await client.get(f"/v2/job-profiles/{profile_id}/questions/generate/status/{token}")).json()
    assert result["status"] == "complete" and result["questionsCount"] == 2


async def test_draft_edit_fences_ai_result_that_finishes_after_publication(client, db, monkeypatch):
    from src.repository.database import async_db
    from src.worker.tasks import generate_questions_task
    from src.services import question_generation
    from src.models.db.job_profile import JobProfile
    _, factory, _ = db
    monkeypatch.setattr(async_db,"get_session",lambda:factory())
    monkeypatch.setattr(job_profiles_v2,"enqueue_job", AsyncMock(return_value="fake-enqueued"))
    waiting, release = asyncio.Event(),asyncio.Event()
    async def slow_generate(**_kwargs):
        waiting.set(); await release.wait()
        return [(1,"easy",{"text":"Stale late output"})]
    monkeypatch.setattr(question_generation,"generate_questions_for_level",slow_generate)
    prefix,profile_id = await authoring_draft(client)
    await client.post(f"/v2/job-profiles/{profile_id}/questions",json={"question":"Original prompt","level":1,"difficulty":"easy"})
    queued = await client.post(f"/v2/job-profiles/{profile_id}/questions/generate",json={"levels":[{"level":1,"count":1}]})
    assert queued.status_code == 202, queued.text
    async with factory() as session:
        revision=(await session.get(JobProfile,profile_id)).content_revision
    task=asyncio.create_task(generate_questions_task({},job_profile_id=profile_id,levels=[{"level":1,"count":1}],generation_token=queued.json()["jobId"],expected_revision=revision))
    await asyncio.wait_for(waiting.wait(),5)
    try:
        # A live job cannot race through publication. Editing stales the job.
        assert (await client.post(f"/v2/{prefix}/{profile_id}/submit")).status_code == 200
        assert (await client.patch(f"/v2/{prefix}/{profile_id}/review",json={"status":"published"})).status_code == 409
        assert (await client.put(f"/v2/{prefix}/{profile_id}",json={"jobName":"Human revision"})).status_code == 200
        assert (await client.post(f"/v2/{prefix}/{profile_id}/submit")).status_code == 200
        published=await client.patch(f"/v2/{prefix}/{profile_id}/review",json={"status":"published"})
        assert published.status_code == 200, published.text
    finally:
        release.set()
    with pytest.raises(fastapi.HTTPException) as error:
        await task
    assert error.value.status_code == 409
    bank=(await client.get(f"/v2/job-profiles/{profile_id}/questions")).json()["questions"]
    assert [q["question"] for q in bank] == ["Original prompt"]


async def test_publication_migration_seals_legacy_rows_and_direct_sql_writers(db):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from src.models.db.job_profile import JobProfile
    engine,factory,user_id=db
    async with factory() as session:
        profile=JobProfile(job_name="Legacy published",job_description="Historical bank",status="published",interview_mode="practice",created_by=user_id)
        session.add(profile);await session.flush();profile_id=profile.id
        q=JobProfileQuestion(job_profile_id=profile_id,question_text="Original legacy question",level=1,difficulty="easy")
        session.add(q);await session.commit();qid=q.id
    path=Path(__file__).parents[2]/"src/repository/migrations/versions/2026_10_19_0300-a9c0d1e2f3a4_seal_publications.py"
    spec=importlib.util.spec_from_file_location("seal_migration",path);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    async with engine.begin() as conn:
        for name in ("generation_result_count","content_revision","generation_state","generation_token","parent_profile_id","publication_snapshot","published_at","authoring_mode"):
            await conn.execute(sa.text(f'ALTER TABLE job_profile DROP COLUMN {name} CASCADE'))
        def upgrade(sync):
            with Operations.context(MigrationContext.configure(sync)): migration.upgrade()
        await conn.run_sync(upgrade)
    async with factory() as session:
        row=await session.get(JobProfile,profile_id)
        assert row.authoring_mode is None and row.interview_mode == "practice"
        assert row.publication_snapshot["questions"][0]["question_text"] == "Original legacy question"
    for sql,params in [
        ("UPDATE job_profile SET job_name='overwrite' WHERE id=:id",{"id":profile_id}),
        ("UPDATE job_profile SET status='draft' WHERE id=:id",{"id":profile_id}),
        ("DELETE FROM job_profile WHERE id=:id",{"id":profile_id}),
        ("UPDATE job_profile_question SET question_text='overwrite' WHERE id=:id",{"id":qid}),
        ("DELETE FROM job_profile_question WHERE id=:id",{"id":qid}),
        ("INSERT INTO job_profile_question(job_profile_id,question_text,level,difficulty) VALUES(:id,'extra',1,'easy')",{"id":profile_id}),
    ]:
        async with engine.connect() as conn:
            with pytest.raises(sa.exc.DBAPIError): await conn.execute(sa.text(sql),params)
            await conn.rollback()
