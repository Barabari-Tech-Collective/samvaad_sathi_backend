import pytest
import datetime
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.models.db.bulk_ats import BulkATSCandidate, BulkATSBatch, BulkATSHandoffStatus
from src.models.db.user import User
from src.services.s3_service import generate_bulk_ats_resume_presigned_url
from src.services.email.bulk_ats_emails import (
    build_student_feedback_email,
    build_company_pitch_email,
)
from src.services.ai_resume.bulk.deepseek_feedback import generate_company_pitch_summary
from src.api.routes.bulk_ats import (
    SendStudentFeedbackEmailBody,
    _candidate_summary,
    _candidate_detail,
)
from src.models.db.notification import Notification


def test_generate_bulk_ats_resume_presigned_url():
    with patch("src.services.s3_service.s3_client") as mock_s3, \
         patch("src.services.s3_service.BUCKET_NAME", "test-bucket"):
        mock_s3.generate_presigned_url.return_value = "https://s3.amazonaws.com/test-bucket/resume.pdf?token=123"

        url = generate_bulk_ats_resume_presigned_url("Samvaad-Saathi/bulk-ats-resumes/test.pdf", expires_in=1800)

        assert url == "https://s3.amazonaws.com/test-bucket/resume.pdf?token=123"
        mock_s3.generate_presigned_url.assert_called_once_with(
            ClientMethod="get_object",
            Params={
                "Bucket": "test-bucket",
                "Key": "Samvaad-Saathi/bulk-ats-resumes/test.pdf",
                "ResponseContentType": "application/pdf",
                "ResponseContentDisposition": "inline",
            },
            ExpiresIn=1800,
        )


def test_build_student_feedback_email_content():
    subject, html, text = build_student_feedback_email(
        candidate_name="Rohit Sharma",
        role_name="Frontend Engineer",
        missing_skills=["TypeScript", "Next.js"],
        broken_links=[{"url": "https://github.com/rohit/demo", "reason": "HTTP 404"}],
        recommendations=["Practice building a full-stack Next.js project."],
        strengths=["Solid HTML/CSS and JavaScript basics"],
        mentor_notes="Candidate shows strong enthusiasm and good code structure.",
    )

    assert "Frontend Engineer" in subject
    assert "Rohit Sharma" in text
    assert "TypeScript" in text
    assert "Next.js" in text
    assert "https://github.com/rohit/demo" in text
    assert "HTTP 404" in text
    assert "Practice building a full-stack Next.js project." in text

    assert "TypeScript" in html
    assert "HTTP 404" in html
    assert "Solid HTML/CSS" in html
    assert "Candidate shows strong enthusiasm" in html


def test_build_company_pitch_email_content():
    candidates = [
        {
            "name": "Aarav Patel",
            "score": 88.5,
            "readiness_score": 9,
            "matched_skills": ["Python", "FastAPI", "PostgreSQL"],
            "resume_url": "https://s3.amazonaws.com/resumes/aarav.pdf",
        }
    ]
    pitch_summary = "We shortlisted Aarav Patel based on exceptional API development skills."

    subject, html, text = build_company_pitch_email(
        company_name="Acme Corp",
        hr_name="Neha Gupta",
        role_name="Backend Developer",
        candidate_summaries=candidates,
        pitch_summary=pitch_summary,
        custom_notes="All candidates are available for immediate joining.",
    )

    assert "Backend Developer" in subject
    assert "Acme Corp" in subject
    assert "Neha Gupta" in text
    assert "Aarav Patel" in text
    assert "Python, FastAPI, PostgreSQL" in text
    assert "https://s3.amazonaws.com/resumes/aarav.pdf" in text
    assert "All candidates are available for immediate joining." in text

    assert "Aarav Patel" in html
    assert "Score: 88.5%" in html
    assert "Readiness: 9/10" in html
    assert "View Resume PDF" in html


@pytest.mark.asyncio
async def test_generate_company_pitch_summary_deterministic_fallback():
    with patch("src.config.manager.settings.DEEPSEEK_API_KEY", ""):
        summary = await generate_company_pitch_summary(
            company_name="TechCorp",
            role_name="Data Analyst",
            candidate_count=3,
            top_skills=["SQL", "Python", "Tableau"],
        )
        assert "TechCorp" in summary
        assert "Data Analyst" in summary
        assert "3" in summary


def test_candidate_serialization_includes_handoff_fields():
    now = datetime.datetime.now(datetime.timezone.utc)
    c = BulkATSCandidate(
        id=42,
        batch_id=1,
        candidate_name="Pooja Verma",
        email_or_candidate_id="pooja@example.com",
        final_score=82.0,
        score_band="strong",
        status="completed",
        needs_review=False,
        s3_key="Samvaad-Saathi/bulk-ats-resumes/test.pdf",
        handoff_status="approved_for_placement",
        program_readiness_score=8,
        program_feedback_notes="Ready for client interviews",
        program_reviewed_by=5,
        program_reviewed_at=now,
        placement_notes="Pitched to Acme",
        placement_assigned_companies=["Acme"],
    )

    summary = _candidate_summary(c)
    assert summary["handoffStatus"] == "approved_for_placement"
    assert summary["programReadinessScore"] == 8
    assert summary["programFeedbackNotes"] == "Ready for client interviews"
    assert summary["programReviewedBy"] == 5
    assert summary["programReviewedAt"] == now.isoformat()
    assert summary["placementNotes"] == "Pitched to Acme"
    assert summary["placementAssignedCompanies"] == ["Acme"]
    assert summary["hasResumeFile"] is True


@pytest.mark.asyncio
async def test_my_notifications_endpoint_includes_metadata():
    from src.api.routes.notifications import my_notifications

    notif = MagicMock()
    notif.id = 1
    notif.kind = "placement_candidate_handoff"
    notif.title = "New Candidates Shared by Program Team"
    notif.body = "SuperAdmin shared 3 candidates with a 9/10 Readiness Score for Placement Review."
    notif.href = "/dashboard/samvaad/bulk-ats/1?view=placement_review"
    notif.created_at = datetime.datetime.now(datetime.timezone.utc)
    notif.read_at = None
    notif.notification_metadata = {
        "batch_id": 1,
        "candidate_ids": [101, 102],
        "readiness_score": 9,
        "target_queue": "Placement Drive Queue",
    }

    mock_session = AsyncMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [notif]

    mock_exec_result = MagicMock()
    mock_exec_result.scalars.return_value = mock_scalars
    mock_exec_result.scalar_one.return_value = 1

    mock_session.execute.return_value = mock_exec_result

    mock_response = MagicMock()
    mock_response.headers = {}
    mock_user = MagicMock()
    mock_user.id = 5

    res = await my_notifications(
        response=mock_response,
        unread_only=False,
        limit=30,
        user=mock_user,
        session=mock_session,
    )

    assert res["unread"] == 1
    assert len(res["items"]) == 1
    item = res["items"][0]
    assert item["metadata"]["batch_id"] == 1
    assert item["metadata"]["candidate_ids"] == [101, 102]
    assert item["metadata"]["readiness_score"] == 9
    assert item["metadata"]["target_queue"] == "Placement Drive Queue"


def test_only_from_informative_error_messages():
    from src.api.routes.bulk_ats_handoff import only_from, SCREENED
    from types import SimpleNamespace

    cand1 = SimpleNamespace(id=101, candidate_name="Davina", handoff_status="referred_to_hr")
    with pytest.raises(HTTPException) as exc1:
        only_from([cand1], SCREENED, "Sharing")
    assert exc1.value.status_code == 409
    assert "Davina has already been handed over to HR" in exc1.value.detail

    cand2 = SimpleNamespace(id=102, candidate_name="Chippe Vinay Kumar", handoff_status="shared_with_placement")
    with pytest.raises(HTTPException) as exc2:
        only_from([cand2], SCREENED, "Sharing")
    assert exc2.value.status_code == 409
    assert "Chippe Vinay Kumar is already shared with the Placement Team" in exc2.value.detail



