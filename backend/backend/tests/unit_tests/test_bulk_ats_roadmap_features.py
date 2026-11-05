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
    ProgramReviewBody,
    PlacementHandoffBody,
    ShareToPlacementBody,
    AcceptPlacementCandidatesBody,
    SendStudentFeedbackEmailBody,
    PitchCompanyEmailBody,
    share_to_placement_endpoint,
    accept_placement_candidates_endpoint,
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


def test_program_review_body_validation():
    with pytest.raises(Exception):
        ProgramReviewBody(program_readiness_score=15)  # must be <= 10

    with pytest.raises(Exception):
        ProgramReviewBody(program_readiness_score=0)   # must be >= 1

    body = ProgramReviewBody(
        program_readiness_score=8,
        program_feedback_notes="Good candidate",
        handoff_status="approved_for_placement",
    )
    assert body.program_readiness_score == 8
    assert body.handoff_status == "approved_for_placement"


def test_placement_handoff_body_validation():
    body = PlacementHandoffBody(
        placement_notes="Scheduled client round",
        placement_assigned_companies=["Google", "Microsoft"],
        handoff_status="referred_to_hr",
    )
    assert body.handoff_status == "referred_to_hr"
    assert len(body.placement_assigned_companies) == 2


def test_share_to_placement_body_validation():
    with pytest.raises(Exception):
        ShareToPlacementBody(candidate_ids=[], readiness_score=8)  # min_length=1

    with pytest.raises(Exception):
        ShareToPlacementBody(candidate_ids=[1], readiness_score=15)  # le=10

    body = ShareToPlacementBody(
        candidate_ids=[101, 102],
        readiness_score=9,
        notes="Top performers",
        target_queue="Placement Drive Queue",
    )
    assert body.candidate_ids == [101, 102]
    assert body.readiness_score == 9
    assert body.notes == "Top performers"
    assert body.target_queue == "Placement Drive Queue"


def test_accept_placement_candidates_body_validation():
    with pytest.raises(Exception):
        AcceptPlacementCandidatesBody(candidate_ids=[])

    with pytest.raises(Exception):
        AcceptPlacementCandidatesBody(candidate_ids=[1], action="invalid_action")

    body = AcceptPlacementCandidatesBody(
        candidate_ids=[101, 102],
        action="accept",
        placement_notes="Added to Capgemini drive",
    )
    assert body.action == "accept"
    assert body.placement_notes == "Added to Capgemini drive"


@pytest.mark.asyncio
async def test_share_to_placement_endpoint_success():
    mock_batch = MagicMock()
    mock_batch.id = 1
    mock_batch.role_name = "Full Stack Engineer"

    c1 = MagicMock()
    c1.id = 101
    c1.batch_id = 1
    c1.handoff_status = "screened"

    c2 = MagicMock()
    c2.id = 102
    c2.batch_id = 1
    c2.handoff_status = "screened"

    current_user = MagicMock()
    current_user.id = 1
    current_user.name = "Uma"
    current_user.email = "uma@example.com"
    current_user.is_admin = False
    current_user.is_super_admin = False
    current_user._token_is_admin = False
    current_user._token_is_super_admin = False

    mock_session = AsyncMock()
    added_notifications = []

    def mock_add(obj):
        added_notifications.append(obj)

    mock_session.add = MagicMock(side_effect=mock_add)
    mock_session.commit = AsyncMock()

    with patch("src.api.routes.bulk_ats._get_authorized_batch", AsyncMock(return_value=mock_batch)), \
         patch("src.repository.crud.bulk_ats.BulkATSCRUDRepository.list_candidates", AsyncMock(return_value=[c1, c2])), \
         patch("src.repository.crud.bulk_ats.BulkATSCRUDRepository.record_audit", AsyncMock()), \
         patch("src.api.routes.bulk_ats._get_placement_recipient_user_ids", AsyncMock(return_value=[2, 3])):

        payload = ShareToPlacementBody(
            candidate_ids=[101, 102],
            readiness_score=9,
            notes="Ready for interview drives.",
            target_queue="Placement Drive Queue",
        )

        res = await share_to_placement_endpoint(
            batch_id=1,
            payload=payload,
            current_user=current_user,
            session=mock_session,
        )

        assert res["status"] == "success"
        assert res["shared_count"] == 2
        assert res["readiness_score"] == 9
        assert c1.handoff_status == "shared_with_placement"
        assert c1.program_readiness_score == 9
        assert c1.program_feedback_notes == "Ready for interview drives."
        assert c2.handoff_status == "shared_with_placement"

        # Check notifications created for placement recipients (user 2 and 3)
        assert len(added_notifications) == 2
        assert added_notifications[0].recipient_id == 2
        assert added_notifications[0].kind == "placement_candidate_handoff"
        assert "Uma" in added_notifications[0].body
        assert "9/10" in added_notifications[0].body
        assert added_notifications[0].href == "/dashboard/samvaad/bulk-ats/1?view=placement_review"
        assert added_notifications[0].notification_metadata["batch_id"] == 1
        assert added_notifications[0].notification_metadata["candidate_ids"] == [101, 102]
        assert added_notifications[0].notification_metadata["readiness_score"] == 9
        assert added_notifications[0].notification_metadata["target_queue"] == "Placement Drive Queue"


@pytest.mark.asyncio
async def test_share_to_placement_endpoint_notifies_super_admin():
    mock_batch = MagicMock()
    mock_batch.id = 1
    mock_batch.role_name = "Full Stack Engineer"

    c1 = MagicMock()
    c1.id = 101
    c1.batch_id = 1
    c1.handoff_status = "screened"

    # Super Admin is initiating the handoff
    super_admin_user = MagicMock()
    super_admin_user.id = 99
    super_admin_user.is_super_admin = True
    super_admin_user.is_admin = True
    super_admin_user.role = "super_admin"
    super_admin_user.name = "Super Admin Uma"
    super_admin_user.email = "admin@example.com"

    mock_session = AsyncMock()
    added_notifications = []

    def mock_add(obj):
        added_notifications.append(obj)

    mock_session.add = MagicMock(side_effect=mock_add)
    mock_session.commit = AsyncMock()

    with patch("src.api.routes.bulk_ats._get_authorized_batch", AsyncMock(return_value=mock_batch)), \
         patch("src.repository.crud.bulk_ats.BulkATSCRUDRepository.list_candidates", AsyncMock(return_value=[c1])), \
         patch("src.repository.crud.bulk_ats.BulkATSCRUDRepository.record_audit", AsyncMock()), \
         patch("src.api.routes.bulk_ats._get_placement_recipient_user_ids", AsyncMock(return_value=[2, 3])), \
         patch("src.api.routes.bulk_ats.is_super_admin_user", return_value=True), \
         patch("src.api.routes.bulk_ats.is_admin_user", return_value=True):

        payload = ShareToPlacementBody(
            candidate_ids=[101],
            readiness_score=10,
            notes="Super Admin approved for placement.",
        )

        res = await share_to_placement_endpoint(
            batch_id=1,
            payload=payload,
            current_user=super_admin_user,
            session=mock_session,
        )

        assert res["status"] == "success"
        # Recipients must include placement staff (2, 3) AND the super admin (99)
        recipient_ids = [n.recipient_id for n in added_notifications]
        assert 99 in recipient_ids
        assert 2 in recipient_ids
        assert 3 in recipient_ids
        assert len(added_notifications) == 3


@pytest.mark.asyncio
async def test_accept_placement_candidates_endpoint_success():
    mock_batch = MagicMock()
    mock_batch.id = 1

    c1 = MagicMock()
    c1.id = 101
    c1.batch_id = 1
    c1.handoff_status = "shared_with_placement"

    current_user = MagicMock()
    current_user.id = 2
    current_user.name = "Placement Staff"

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    with patch("src.api.routes.bulk_ats._get_authorized_batch", AsyncMock(return_value=mock_batch)), \
         patch("src.repository.crud.bulk_ats.BulkATSCRUDRepository.list_candidates", AsyncMock(return_value=[c1])), \
         patch("src.repository.crud.bulk_ats.BulkATSCRUDRepository.record_audit", AsyncMock()):

        payload = AcceptPlacementCandidatesBody(
            candidate_ids=[101],
            action="accept",
            placement_notes="Shortlisted for client drive",
        )

        res = await accept_placement_candidates_endpoint(
            batch_id=1,
            payload=payload,
            current_user=current_user,
            session=mock_session,
        )

        assert res["status"] == "success"
        assert res["action"] == "accept"
        assert res["handoff_status"] == "accepted_in_placement"
        assert c1.handoff_status == "accepted_in_placement"
        assert c1.placement_notes == "Shortlisted for client drive"


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


