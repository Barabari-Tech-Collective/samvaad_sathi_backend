import inspect
from types import SimpleNamespace
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession

from src.services.summary_report_v2 import SummaryReportServiceV2


def test_generate_for_interview_lite_has_no_hardcoded_mock_shortcut():
    """Regression guard: generate_for_interview_lite must always compute a
    real report from the interview's actual questions/attempts. It must
    never contain a hardcoded mock-report shortcut keyed off the track
    string (e.g. 'hr'/'full stack'/'node'), which previously caused every
    Full Stack Developer report to return fake, generic scores unrelated to
    the candidate's actual performance."""
    source = inspect.getsource(SummaryReportServiceV2.generate_for_interview_lite)
    assert "mock_report" not in source
    assert "DEMO HARDCODE" not in source
    assert "is_hr" not in source


def test_missing_per_question_feedback_uses_honest_fallback():
    """Regression guard: when the LLM response has no feedback entry for an
    attempted question, the fallback text must read as a fallback, not as
    personalized coaching advice (see src/services/summary_report_v2.py
    lines ~1119-1122)."""
    service = SummaryReportServiceV2(db=cast(SQLAlchemyAsyncSession, None))

    question = SimpleNamespace(id=1, category="tech", text="What is JavaScript?")
    attempt = SimpleNamespace(transcription={"text": "some answer"}, analysis_json={})

    result = service._calculate_final_scores_lite(
        llm_data={"perQuestionScores": [], "perQuestionFeedback": []},
        total_questions=1,
        all_questions=[question],
        attempts_by_question_id={1: attempt},
        actually_attempted_question_ids={1},
        track="tech",
        interview_date="2026-01-01",
        candidate_name="Test Candidate",
        duration_str="10 mins",
        duration_feedback="Good pace",
    )

    feedback = result["questionAnalysis"][0]["feedback"]
    assert feedback["strengths"] == "Feedback unavailable for this answer."
    assert feedback["areasOfImprovement"] == (
        "Detailed feedback could not be generated — please review this question manually."
    )
    # Regression guard: must not silently read like real personalized coaching.
    assert "review core concepts" not in feedback["areasOfImprovement"].lower()
    assert "answer provided and recorded" not in feedback["strengths"].lower()


def test_present_per_question_feedback_is_used_as_is():
    """When the LLM does return feedback for a question, it should be used
    verbatim rather than overridden by the fallback."""
    service = SummaryReportServiceV2(db=cast(SQLAlchemyAsyncSession, None))

    question = SimpleNamespace(id=7, category="behavioral", text="Tell me about a challenge you faced.")
    attempt = SimpleNamespace(transcription={"text": "a real answer"}, analysis_json={})

    llm_data = {
        "perQuestionScores": [{"questionId": 7, "knowledgeScores": {}, "speechScores": {}}],
        "perQuestionFeedback": [
            {"strengths": "Clear example given.", "areasOfImprovement": "Could quantify the result."}
        ],
    }

    result = service._calculate_final_scores_lite(
        llm_data=llm_data,
        total_questions=1,
        all_questions=[question],
        attempts_by_question_id={7: attempt},
        actually_attempted_question_ids={7},
        track="behavioral",
        interview_date="2026-01-01",
        candidate_name="Test Candidate",
        duration_str="10 mins",
        duration_feedback="Good pace",
    )

    feedback = result["questionAnalysis"][0]["feedback"]
    assert feedback["strengths"] == "Clear example given."
    assert feedback["areasOfImprovement"] == "Could quantify the result."


def test_calculate_final_scores_never_produces_negative_criterion():
    """Regression guard: with partial completion (1 of 5 questions attempted)
    and rounding-sensitive scores, the remainder criterion (terminology/
    grammar) must never go negative. See src/services/summary_report_v2.py
    _calculate_final_scores, ~lines 407-422."""
    service = SummaryReportServiceV2(db=cast(SQLAlchemyAsyncSession, None))

    question = SimpleNamespace(id=1, category="tech", text="What is a closure?")

    llm_data = {
        "perQuestionScores": [
            {
                "questionId": 1,
                "knowledgeScores": {"accuracy": 3, "depth": 3, "relevance": 3, "examples": 3, "terminology": 3},
                "speechScores": {"fluency": 3, "structure": 3, "pacing": 3, "grammar": 1},
            }
        ],
        "perQuestionFeedback": [],
    }

    result = service._calculate_final_scores(
        llm_data=llm_data,
        total_questions=5,  # only 1 of 5 attempted -> completion_ratio = 0.2, forces rounding-loss
        all_questions=[question],
        attempts_by_question_id={},
        actually_attempted_question_ids={1},
        track="tech",
        interview_date="2026-01-01",
        candidate_name="Test Candidate",
    )

    criteria = result["scoreSummary"]["knowledgeCompetence"]["criteria"]
    speech_criteria = result["scoreSummary"]["speechAndStructure"]["criteria"]
    assert criteria["terminology"] >= 0
    assert speech_criteria["grammar"] >= 0


def test_build_fallback_report_never_produces_negative_criterion():
    """Same regression guard as above, but for the DB-driven fallback report
    used when the LLM call fails entirely (_build_fallback_report,
    ~lines 550-565) — this is the path actually hit in production LLM
    outages, so it matters most."""
    service = SummaryReportServiceV2(db=cast(SQLAlchemyAsyncSession, None))

    question = SimpleNamespace(id=1, category="tech", text="What is a closure?")

    computed_metrics = {
        "kc_accuracy_pct": 60,  # -> 3/5
        "kc_depth_pct": 60,
        "kc_relevance_pct": 60,
        "kc_examples_pct": 60,
        "kc_terminology_pct": 60,
        "ssf_fluency_pct": 60,
        "ssf_structure_pct": 60,
        "ssf_pacing_pct": 60,
        "ssf_grammar_pct": 20,  # -> 1/5, deliberately lopsided to force a rounding remainder
        "total_questions": 5,
        "attempted_questions": 1,
    }

    result = service._build_fallback_report(
        interview_id=1,
        track="tech",
        computed_metrics=computed_metrics,
        all_questions=[question],
        attempts_map={},
        interview_date="2026-01-01",
        candidate_name="Test Candidate",
    )

    score_summary = result["scoreSummary"] if isinstance(result, dict) else result["scoreSummary"]
    criteria = score_summary["knowledgeCompetence"]["criteria"]
    speech_criteria = score_summary["speechAndStructure"]["criteria"]
    assert criteria["terminology"] >= 0
    assert speech_criteria["grammar"] >= 0
