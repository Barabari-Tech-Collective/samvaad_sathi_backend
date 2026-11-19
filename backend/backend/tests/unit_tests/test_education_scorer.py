import pytest
from src.services.ai_resume.scoring.education_scorer import EducationScorer
from src.services.ai_resume.bulk.evidence import build_category_evidence


def test_education_scorer_with_explicit_grade():
    scorer = EducationScorer()
    records = [
        {
            "institution": "National Institute of Technology",
            "duration": "2019 - 2023",
            "grade": "8.7 CGPA",
        }
    ]
    report = scorer.score_education(records)
    assert report["totalScore"] == 10
    assert report["educationAnalysis"]["institution"]["present"] is True
    assert report["educationAnalysis"]["institution"]["score"] == 3
    assert report["educationAnalysis"]["duration"]["present"] is True
    assert report["educationAnalysis"]["duration"]["score"] == 3
    assert report["educationAnalysis"]["grade"]["present"] is True
    assert report["educationAnalysis"]["grade"]["score"] == 4
    assert "8.7" in report["educationAnalysis"]["grade"]["detectedValue"]


def test_education_scorer_with_cgpa_field():
    scorer = EducationScorer()
    records = [
        {
            "institution": "Delhi Technological University",
            "duration": "2018 - 2022",
            "cgpa": "8.45",
        }
    ]
    report = scorer.score_education(records)
    assert report["totalScore"] == 10
    assert report["educationAnalysis"]["grade"]["present"] is True
    assert report["educationAnalysis"]["grade"]["score"] == 4


def test_education_scorer_with_percentage_and_marks():
    scorer = EducationScorer()
    records = [
        {
            "institution": "Anna University Chennai",
            "duration": "2017 - 2021",
            "percentage": "82.5%",
        }
    ]
    report = scorer.score_education(records)
    assert report["totalScore"] == 10
    assert report["educationAnalysis"]["grade"]["present"] is True
    assert report["educationAnalysis"]["grade"]["score"] == 4


def test_education_scorer_with_embedded_grade_in_degree():
    scorer = EducationScorer()
    records = [
        {
            "institution": "Vellore Institute of Technology",
            "duration": "2020 - 2024",
            "degree": "B.Tech in Computer Science (CGPA: 9.1)",
        }
    ]
    report = scorer.score_education(records)
    assert report["totalScore"] == 10
    assert report["educationAnalysis"]["grade"]["present"] is True
    assert report["educationAnalysis"]["grade"]["score"] == 4
    assert "9.1" in report["educationAnalysis"]["grade"]["detectedValue"]


def test_education_scorer_fallback_to_raw_resume_text():
    scorer = EducationScorer()
    records = [
        {
            "institution": "Kakatiya Institute of Technology & Science",
            "duration": "2020 - 2024",
        }
    ]
    raw_text = """
    EDUCATION
    Kakatiya Institute of Technology & Science, Warangal
    B.Tech in Computer Science and Engineering | 2020 - 2024
    Cumulative CGPA: 8.65 / 10
    """
    report = scorer.score_education(records, raw_resume_text=raw_text)
    assert report["totalScore"] == 10
    assert report["educationAnalysis"]["grade"]["present"] is True
    assert report["educationAnalysis"]["grade"]["score"] == 4
    assert "8.65" in report["educationAnalysis"]["grade"]["detectedValue"]
    # Verify the grade is backfilled into records for display
    assert records[0].get("grade") is not None


def test_build_category_evidence_includes_cgpa():
    evidence = build_category_evidence(
        parsed_resume_json={
            "education": [
                {
                    "degree": "B.Tech",
                    "institution": "Kakatiya Institute",
                    "field": "CSE",
                    "grade": "8.7 CGPA",
                }
            ],
            "skills": ["Python"],
        },
        required_skills=["Python"],
        preferred_skills=[],
        link_report={},
        jev_report={},
    )
    edu_evidence = evidence.get("education", [])
    assert len(edu_evidence) > 0
    text = edu_evidence[0]["text"]
    assert "Kakatiya Institute" in text
    assert "8.7 CGPA" in text


def test_build_category_evidence_surfaces_deterministic_report_cgpa():
    evidence = build_category_evidence(
        parsed_resume_json={
            "education": [
                {
                    "degree": "B.Tech",
                    "institution": "Kakatiya Institute",
                    "field": "CSE",
                }
            ],
            "skills": ["Python"],
        },
        required_skills=["Python"],
        preferred_skills=[],
        link_report={},
        jev_report={},
        deterministic_report={
            "deterministicMetrics": {
                "educationModule": {
                    "educationAnalysis": {
                        "grade": {
                            "present": True,
                            "detectedValue": "CGPA: 8.7",
                        }
                    }
                }
            }
        },
    )
    edu_evidence = evidence.get("education", [])
    combined_text = " ".join(item["text"] for item in edu_evidence)
    assert "8.7" in combined_text


def test_candidate_detail_exposes_parsed_education():
    import datetime
    from types import SimpleNamespace
    from src.api.routes import bulk_ats as bulk_ats_routes

    candidate = SimpleNamespace(
        id=99,
        candidate_name="Test Student",
        email_or_candidate_id="student@test.org",
        final_score=75,
        score_band="good",
        status="completed",
        needs_review=False,
        warnings=[],
        confidence_score=0.85,
        score_lower_bound=None,
        score_upper_bound=None,
        reviewer_decision=None,
        interview_outcome=None,
        assigned_reviewer_id=None,
        parsed_resume_json={
            "skills": ["Python"],
            "projects": [],
            "education": [
                {
                    "institution": "IIT Madras",
                    "degree": "B.Tech",
                    "field": "Electrical",
                    "duration": "2019-2023",
                    "cgpa": "8.8",
                }
            ],
        },
        original_filename="test.pdf",
        resume_url=None,
        batch=SimpleNamespace(role_name="Software Engineer"),
        error_message=None,
        deterministic_report={},
        jev_report={},
        link_report={},
        reviewer_notes=None,
        decision_reason=None,
        outcome_recorded_at=None,
        created_at=datetime.datetime.now(datetime.timezone.utc),
        updated_at=datetime.datetime.now(datetime.timezone.utc),
    )

    detail = bulk_ats_routes._candidate_detail(candidate)
    assert "parsedEducation" in detail
    assert len(detail["parsedEducation"]) == 1
    assert detail["parsedEducation"][0]["institution"] == "IIT Madras"
    assert detail["parsedEducation"][0]["grade"] == "8.8"


def test_hygiene_check_includes_education_score():
    from src.services.ai_resume.scoring.ats_calculator import ATSCalculator
    calc = ATSCalculator()
    education_report = {
        "educationAnalysis": {
            "institution": {"present": True},
            "duration": {"present": True},
            "grade": {"present": True, "detectedValue": "CGPA: 9.0"},
        }
    }
    hygiene = calc._generate_hygiene_snapshot(
        link_report={"categoryBreakdown": {}},
        education_report=education_report,
        raw_resume_text="Contact: test@example.com, +91 9876543210",
    )
    assert hygiene["hasEmail"] is True
    assert hygiene["hasPhone"] is True
    assert hygiene["hasDuration"] is True
    assert hygiene["hasScore"] is True
    assert hygiene["hasCgpaOrPercentage"] is True
    assert hygiene["detectedScore"] == "CGPA: 9.0"
    assert "CGPA: 9.0" in hygiene["scoreMessage"]


