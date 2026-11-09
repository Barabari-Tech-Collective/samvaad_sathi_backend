"""What the question generator asks the model for HR, non-tech, and bank-led interviews."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.config.manager import settings
from src.services.knowledge_parser import reference_questions_for_level
from src.services.llm import generate_interview_questions_with_llm
from src.services.syllabus_service import syllabus_service

ENGINEERING_PHRASES = ("on-call", "at scale", "live-system", "architectural", "production experience")
TECHNICAL_JD = "Build distributed microservices on Kubernetes for our backend platform."
BANK = json.dumps(
    [
        {
            "topicName": "Attitude",
            "levels": [
                {"level": 1, "questions": ["Tell me about yourself."]},
                {"level": 4, "questions": ["Describe your biggest failure."]},
            ],
        }
    ]
)


async def _prompts(track, category, level, *, bank=None, experience="fresher", skills=("Python", "AWS")):
    difficulty = {1: "easy", 2: "medium", 3: "hard", 4: "expert"}[level]
    ctx = syllabus_service.resolve_generation_context(
        track=track,
        category=category,
        difficulty=difficulty,
        context_text=TECHNICAL_JD,
        skills_list=list(skills),
        experience_level=experience,
    )
    influence = {**ctx["influence"], "level": level}
    if bank:
        influence["knowledge_reference_context"] = bank
    original = settings.OPENAI_API_KEY
    settings.OPENAI_API_KEY = "test-fake-key"
    try:
        with patch("src.services.llm.structured_output", new_callable=AsyncMock) as mock:
            mock.return_value = (None, None, 1, "gpt-4o-mini")
            await generate_interview_questions_with_llm(
                track=track,
                context_text=TECHNICAL_JD,
                count=5,
                difficulty=difficulty,
                syllabus_topics=ctx["topics"],
                ratio=ctx["ratio"],
                influence=influence,
            )
    finally:
        settings.OPENAI_API_KEY = original
    kwargs = mock.call_args.kwargs
    return kwargs["system_prompt"], kwargs["user_content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [1, 2, 3, 4])
@pytest.mark.parametrize(
    "track,category", [("HR Interview", "hr"), ("Software Engineer", "hr"), ("Sales Executive", "sales")]
)
async def test_non_tech_prompts_never_use_engineering_level_rules(track, category, level):
    system, user = await _prompts(track, category, level)
    combined = (system + json.dumps(user)).lower()
    for phrase in ENGINEERING_PHRASES:
        assert phrase not in combined
    assert "debug" not in json.dumps(user["constraints"]).lower()
    assert user["depth_guidelines"] == []


@pytest.mark.asyncio
async def test_hr_round_is_about_the_person_not_the_hr_function():
    system, user = await _prompts("Software Engineer", "hr", 3)
    assert "general HR round" in system
    assert "hr_operations" not in system
    # Skills on the profile must not turn into question topics.
    assert user["syllabus"]["domain_topics"] == []
    assert "skills" not in user["influence"]


@pytest.mark.asyncio
async def test_technical_level_rules_are_unchanged_for_technical_roles():
    system, _ = await _prompts("Backend Developer", "it", 3, experience="5+")
    assert "You are on-call" in system
    assert "FRESHER RULE" not in system


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "experience,expected",
    [("fresher", True), ("0-1", True), ("0–1", True), ("Entry level", True), ("1-2", False), (None, False)],
)
async def test_fresher_rule_follows_experience_level(experience, expected):
    system, _ = await _prompts("HR Interview", "hr", 2, experience=experience)
    assert ("FRESHER RULE" in system) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "track,category", [("HR Interview", "hr"), ("Sales Executive", "sales")]
)
async def test_description_is_background_only_for_non_tech(track, category):
    system, _ = await _prompts(track, category, 1)
    assert "JOB DESCRIPTION IS BACKGROUND ONLY" in system
    assert "provided job description" not in system


@pytest.mark.asyncio
async def test_only_the_matching_levels_bank_reaches_the_model():
    system, user = await _prompts("HR Interview", "hr", 1, bank=BANK)
    combined = system + json.dumps(user)
    assert "Tell me about yourself." in combined
    assert "Describe your biggest failure." not in combined
    assert "knowledge_reference_context" not in user["influence"]
    assert "Reference Question Bank (main source)" in system
    assert "STRICT CATEGORY DISTRIBUTION MANDATE" not in system
    assert user["syllabus"] == {}


def test_bank_filter_keeps_unusual_uploads():
    assert reference_questions_for_level("plain text bank", 2) == "plain text bank"
    assert reference_questions_for_level(BANK, 3) == BANK
    assert reference_questions_for_level(BANK, 0) == BANK
    assert reference_questions_for_level(None, 1) == ""
    assert "Describe your biggest failure." in reference_questions_for_level(BANK, 4)


@pytest.mark.asyncio
async def test_regenerate_uses_the_questions_level_and_the_bank():
    from types import SimpleNamespace

    from src.api.routes.job_profiles_v2 import regenerate_job_profile_question_v2
    from src.models.schemas.job_profile import JobProfileRegenerateQuestionRequest

    question = SimpleNamespace(
        id=1, job_profile_id=9, level=4, difficulty="expert", question_type="self",
        question_text="Old", is_ai_generated=True,
    )
    profile = SimpleNamespace(
        job_name="HR Interview", job_description=TECHNICAL_JD, skills=[],
        category="hr", experience_level="fresher", content_revision=1,
    )
    repo = SimpleNamespace(
        require_unpublished=lambda _profile: None,
        async_session=SimpleNamespace(rollback=AsyncMock()),
        get_question_by_id=AsyncMock(return_value=question),
        get_by_id=AsyncMock(return_value=profile),
        update_job_profile_question=AsyncMock(return_value=question),
    )
    item = {"text": "New", "category": "self"}
    with patch(
        "src.api.routes.job_profiles_v2.generate_interview_questions_with_llm",
        new_callable=AsyncMock,
        return_value=(["New"], None, 1, "m", [item]),
    ) as generate:
        await regenerate_job_profile_question_v2(
            question_id=1,
            payload=JobProfileRegenerateQuestionRequest(knowledge_reference_context=BANK),
            current_user=None,
            job_profile_repo=repo,
        )
    influence = generate.call_args.kwargs["influence"]
    assert influence["level"] == 4
    assert influence["knowledge_reference_context"] == BANK
