import pytest
from src.services.syllabus_service import syllabus_service


def test_resolve_generation_context_for_hr_role():
    context = syllabus_service.resolve_generation_context(
        track="HR Executive",
        category="hr",
        difficulty="easy",
        context_text="Responsible for talent acquisition, employee onboarding, and payroll compliance.",
        skills_list=["Talent Acquisition", "Employee Onboarding", "Payroll Compliance"],
        experience_level="mid",
    )

    assert context["is_non_tech"] is True
    assert context["target_role"] == "HR Executive"
    
    # Critical guarantee: tech topic list MUST be empty for HR
    assert context["topics"]["tech"] == []
    
    # Skills must be preserved in tech_allied
    assert "Talent Acquisition" in context["topics"]["tech_allied"]
    assert "Payroll Compliance" in context["topics"]["tech_allied"]

    # Category ratio must be HR specific
    assert "hr_operations" in context["ratio"]
    assert "talent_acquisition" in context["ratio"]
    assert "behavioral" in context["ratio"]
    assert context["ratio"]["hr_operations"] == 2
    assert context["ratio"]["talent_acquisition"] == 2
    assert context["ratio"]["behavioral"] == 1

    # Influence flags
    assert context["influence"]["is_non_tech"] is True
    assert context["influence"]["target_role"] == "HR Executive"


def test_resolve_generation_context_for_recruiter_by_title_only():
    # Even if category is omitted or empty, title containing 'recruiter' triggers HR domain
    context = syllabus_service.resolve_generation_context(
        track="Senior Technical Recruiter",
        category=None,
        difficulty="medium",
        skills_list=["Sourcing", "Interviewing"],
    )

    assert context["is_non_tech"] is True
    assert context["topics"]["tech"] == []
    assert "hr_operations" in context["ratio"]
    assert "talent_acquisition" in context["ratio"]


def test_resolve_generation_context_for_sales():
    context = syllabus_service.resolve_generation_context(
        track="Business Development Executive",
        category="sales",
        difficulty="easy",
        skills_list=["Lead Generation", "Cold Calling", "Negotiation"],
    )

    assert context["is_non_tech"] is True
    assert context["topics"]["tech"] == []
    assert "sales_strategy" in context["ratio"]
    assert "client_management" in context["ratio"]
    assert "behavioral" in context["ratio"]


def test_resolve_generation_context_for_python_developer():
    context = syllabus_service.resolve_generation_context(
        track="Python Developer",
        category="engineering",
        difficulty="medium",
        context_text="Looking for a Python Backend developer with Django experience.",
        skills_list=["Python", "Django", "PostgreSQL"],
    )

    assert context["is_non_tech"] is False
    assert context["target_role"] == "Python Developer"
    
    # Tech topics must reflect Python skills, NOT JavaScript Developer syllabus
    assert context["topics"]["tech"] == ["Python", "Django", "PostgreSQL"]
    assert "DOM" not in context["topics"]["tech"]
    assert "Booleans" not in context["topics"]["tech"]
    assert "Closures" not in context["topics"]["tech"]

    # Ratio has standard tech split
    assert "tech" in context["ratio"]
    assert "tech_allied" in context["ratio"]
    assert "behavioral" in context["ratio"]


def test_resolve_generation_context_for_full_stack_python():
    # If a full stack role explicitly mentions Python, it should not default to MERN Stack
    context = syllabus_service.resolve_generation_context(
        track="Full Stack Developer",
        category="engineering",
        difficulty="hard",
        skills_list=["Python", "FastAPI", "React", "Docker"],
    )

    assert context["is_non_tech"] is False
    # Not aliased to MERN Stack Developer because skills contain Python
    assert context["target_role"] == "Full Stack Developer"
    assert "Python" in context["topics"]["tech"]
    assert "FastAPI" in context["topics"]["tech"]


def test_resolve_generation_context_for_mern_stack():
    context = syllabus_service.resolve_generation_context(
        track="MERN Stack Developer",
        category="engineering",
        difficulty="medium",
        skills_list=["React", "Node", "Express", "MongoDB"],
    )

    assert context["is_non_tech"] is False
    assert context["target_role"] == "MERN Stack Developer"
    assert len(context["topics"]["tech"]) > 0


@pytest.mark.asyncio
async def test_llm_prompt_allocates_exact_question_count_in_category_mix():
    from unittest.mock import patch, AsyncMock
    from src.services.llm import generate_interview_questions_with_llm
    from src.config.manager import settings

    orig_key = settings.OPENAI_API_KEY
    settings.OPENAI_API_KEY = "test-fake-key"
    try:
        with patch("src.services.llm.structured_output", new_callable=AsyncMock) as mock_out:
            mock_out.return_value = (None, None, 10, "gpt-4o-mini")
            
            # Test batch of 10 questions for HR
            await generate_interview_questions_with_llm(
                track="HR Executive",
                count=10,
                difficulty="medium",
                influence={"category": "hr", "is_non_tech": True}
            )

            call_kwargs = mock_out.call_args.kwargs
            sys_prompt = call_kwargs["system_prompt"]

            # Must allocate exactly 10 questions (4 + 4 + 2 = 10), NEVER hardcoded 2+2+1=5
            assert "generating a set of exactly 10 interview questions" in sys_prompt
            assert "HR Operations & Compliance (hr_operations): 4 questions" in sys_prompt
            assert "Talent Acquisition & People Strategy (talent_acquisition): 4 questions" in sys_prompt
            assert "Behavioral & Employee Relations (behavioral): 2 questions" in sys_prompt
            assert "STRICT NON-TECHNICAL MANDATE" in sys_prompt
    finally:
        settings.OPENAI_API_KEY = orig_key


@pytest.mark.asyncio
async def test_llm_prompt_largest_remainder_edge_case_preserves_behavioral():
    from unittest.mock import patch, AsyncMock
    from src.services.llm import generate_interview_questions_with_llm
    from src.config.manager import settings

    orig_key = settings.OPENAI_API_KEY
    settings.OPENAI_API_KEY = "test-fake-key"
    try:
        with patch("src.services.llm.structured_output", new_callable=AsyncMock) as mock_out:
            mock_out.return_value = (None, None, 4, "gpt-4o-mini")

            # Request exactly 4 questions for a tech role (weights: tech: 2, tech_allied: 2, behavioral: 1)
            # Under greedy rounding, behavioral was zeroed out (2, 2, 0).
            # Under largest remainder method (Hare-Niemeyer), it allocates (2, 1, 1).
            await generate_interview_questions_with_llm(
                track="Python Developer",
                count=4,
                difficulty="medium",
                influence={"category": "engineering", "is_non_tech": False},
            )

            call_kwargs = mock_out.call_args.kwargs
            sys_prompt = call_kwargs["system_prompt"]

            assert "generating a set of exactly 4 interview questions" in sys_prompt
            assert "Tech (tech): 2 questions" in sys_prompt
            assert "Tech Allied (tech_allied): 1 question" in sys_prompt
            assert "Behavioral (behavioral): 1 question" in sys_prompt
    finally:
        settings.OPENAI_API_KEY = orig_key


def test_derive_canonical_role_javascript_not_blocked_by_java():
    # "JavaScript Developer" must not be blocked by "java" substring
    canonical = syllabus_service.derive_canonical_role("JavaScript Developer")
    assert canonical == "JavaScript Developer"

    context = syllabus_service.resolve_generation_context(
        track="JavaScript Developer",
        category="engineering",
        difficulty="easy",
    )
    assert context["is_non_tech"] is False
    assert context["target_role"] == "JavaScript Developer"
    # Curated JS topics from syllabus must be loaded
    assert any("JavaScript" in t for t in context["topics"]["tech"])
    assert any("Booleans" in t for t in context["topics"]["tech"])


def test_derive_canonical_role_mongodb_not_blocked_by_go():
    # Candidate skills with "MongoDB" must not trigger "go"
    canonical = syllabus_service.derive_canonical_role(
        track="MERN Stack Developer",
        skills=["React", "Node", "Express", "MongoDB"],
    )
    assert canonical == "MERN Stack Developer"

    context = syllabus_service.resolve_generation_context(
        track="MERN Stack Developer",
        category="engineering",
        difficulty="easy",
        skills_list=["React", "Node", "Express", "MongoDB"],
    )
    assert context["is_non_tech"] is False
    assert context["target_role"] == "MERN Stack Developer"
    assert len(context["topics"]["tech"]) > 0


def test_derive_canonical_role_full_stack_with_python_not_aliased_to_mern():
    # Generic "Full Stack Developer" with Python/FastAPI must NOT alias to MERN
    canonical = syllabus_service.derive_canonical_role(
        track="Full Stack Developer",
        skills=["Python", "FastAPI"],
    )
    assert canonical is None

    context = syllabus_service.resolve_generation_context(
        track="Full Stack Developer",
        category="engineering",
        difficulty="medium",
        skills_list=["Python", "FastAPI"],
    )
    assert context["is_non_tech"] is False
    assert context["target_role"] == "Full Stack Developer"
    assert context["topics"]["tech"] == ["Python", "FastAPI"]


