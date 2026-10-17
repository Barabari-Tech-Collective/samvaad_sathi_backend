from types import SimpleNamespace

from src.models.schemas.summary_report_v2 import SummaryReportResponseLite
from src.services.practice_report_contract import apply_report_contract


def _attempt(question_id: int, *, confidence: float = 0.9, analyzed: bool = True):
    return SimpleNamespace(
        question_id=question_id,
        answer_saved_at=True,
        audio_url=f"answers/{question_id}.webm",
        transcription={
            "text": "I explained the situation action and measurable result clearly",
            "confidence": confidence,
        },
        analysis_json=(
            {"domain": {"criteria": {"relevance": {"score": 72}}}} if analyzed else {}
        ),
    )


def _raw_report(total: int = 3):
    return {
        "reportId": "temporary",
        "candidateInfo": {
            "name": "Student",
            "interviewDate": "2026-10-18T00:00:00Z",
            "roleTopic": "Data analyst",
        },
        "scoreSummary": {
            "knowledgeCompetence": {"percentage": 70},
            "speechAndStructure": {"percentage": 62},
        },
        "questionAnalysis": [
            {
                "id": index + 1,
                "totalQuestions": total,
                "type": "Behavioral question",
                "question": f"Question {index + 1}",
                "feedback": {
                    "strengths": "The example was relevant.",
                    "areasOfImprovement": "Slow your pace and use a pause.",
                },
            }
            for index in range(total)
        ],
    }


def test_contract_hides_scores_and_drill_below_evidence_threshold():
    questions = [SimpleNamespace(id=index + 1) for index in range(3)]
    attempts = {1: _attempt(1), 2: _attempt(2)}

    report = apply_report_contract(
        _raw_report(),
        interview_id=12,
        attempts_by_question_id=attempts,
        ordered_questions=questions,
        generated_at="2026-10-18T00:00:00Z",
        llm_succeeded=True,
    )

    assert report["reportStatus"] == "insufficient_evidence"
    assert report["scoreSummary"] is None
    assert report["recommendedPractice"] is None
    assert report["evidenceSummary"]["evaluatedAnswers"] == 2
    assert "not shown" in report["uncertaintyNotice"]


def test_contract_maps_recommendation_to_real_drill_route():
    questions = [SimpleNamespace(id=index + 1) for index in range(3)]
    attempts = {index: _attempt(index) for index in range(1, 4)}

    report = apply_report_contract(
        _raw_report(),
        interview_id=42,
        attempts_by_question_id=attempts,
        ordered_questions=questions,
        generated_at="2026-10-18T00:00:00Z",
        llm_succeeded=True,
    )

    assert report["reportStatus"] == "ready"
    assert report["recommendedPractice"]["module"] == "pace"
    assert report["recommendedPractice"]["href"] == "/control-your-pace"
    assert report["recommendedPractice"]["evidenceAnswerCount"] == 3
    assert report["evidenceSummary"]["confidence"] == "high"


def test_low_confidence_answer_is_excluded_and_report_id_is_idempotent():
    questions = [SimpleNamespace(id=index + 1) for index in range(3)]
    attempts = {1: _attempt(1), 2: _attempt(2), 3: _attempt(3, confidence=0.1)}
    kwargs = dict(
        interview_id=99,
        attempts_by_question_id=attempts,
        ordered_questions=questions,
        generated_at="2026-10-18T00:00:00Z",
        llm_succeeded=True,
    )

    first = apply_report_contract(_raw_report(), **kwargs)
    second = apply_report_contract(_raw_report(), **kwargs)

    assert first["reportId"] == second["reportId"]
    assert first["evidenceSummary"]["evaluatedAnswers"] == 2
    assert first["questionAnalysis"][2]["evidence"]["status"] == "unavailable"
    assert first["questionAnalysis"][2]["feedback"] is None


def test_existing_report_without_gate_four_metadata_still_validates():
    """Reports saved before Gate 4 must remain readable from interview history."""

    legacy_report = {
        "reportId": "existing-report",
        "candidateInfo": {
            "name": "Student",
            "interviewDate": "2026-10-01T00:00:00Z",
            "roleTopic": "Data analyst",
        },
        "scoreSummary": {
            "speechAndStructure": {
                "score": 12,
                "maxScore": 20,
                "average": 3,
                "maxAverage": 5,
                "percentage": 60,
                "criteria": {
                    "fluency": 3,
                    "structure": 3,
                    "pacing": 3,
                    "grammar": 3,
                },
            }
        },
        "questionAnalysis": [],
        "recommendedPractice": {
            "title": "Structure your answers",
            "description": "Use a clear beginning, middle and end.",
        },
    }

    validated = SummaryReportResponseLite(**legacy_report)

    assert validated.reportStatus == "ready"
    assert validated.evidenceSummary is None
    assert validated.recommendedPractice is not None
    assert validated.recommendedPractice.href is None
