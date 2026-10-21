import datetime
import io
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pydantic
import pytest
from fastapi import HTTPException

from src.api.routes.bulk_ats import (
    _cell_text,
    _feedback_states,
    _format_feedback_copy,
    _get_authorized_batch,
    _get_authorized_candidate,
    _parse_skills,
    _parse_resume_urls,
    _queue_candidate_feedback,
    _require_role_rubric,
)
from src.api.routes import bulk_ats as bulk_ats_routes
from src.config.manager import settings
from src.main import backend_app
from src.repository.crud.bulk_ats import BulkATSCRUDRepository
from src.repository.crud.bulk_ats import _feedback_request_is_active
from src.services.ai_resume.bulk import (
    deepseek_feedback,
    google_drive_oauth,
    jev_client,
    pipeline,
)
from src.services.ai_resume.bulk.evidence import (
    build_category_evidence,
    calculate_confidence,
    redact_for_semantic_scoring,
)
from src.services.ai_resume.bulk.jd_quality import analyze_job_description
from src.services.ai_resume.bulk.domain_profile import (
    explicit_link_requirements,
    infer_domain_profile,
)
from src.services.ai_resume.bulk.link_requirements import check_link_requirements
from src.services.ai_resume.bulk.scoring_weights import resolve_weights
from src.services.ai_resume.bulk.malware_scan import scan_document
from src.services.ai_resume.scoring.project_scorer import ProjectScorer
from src.services.ai_resume.url_validator import extract_professional_urls
from src.utilities.link_validator import SmartLinkValidator
from src.worker import tasks as worker_tasks
from src.worker.tasks import (
    _detect_resume_type,
    _fetch_resume_from_url,
    _google_drive_download_url,
)


def test_cell_text_accepts_numeric_excel_candidate_ids():
    assert _cell_text(1234) == "1234"
    assert _cell_text(1234.0) == "1234"
    assert _cell_text("  candidate-7  ") == "candidate-7"
    assert _cell_text(None) == ""


def test_parse_skills_deduplicates_and_accepts_newlines():
    assert _parse_skills("Python, FastAPI\npython, PostgreSQL") == [
        "Python",
        "FastAPI",
        "PostgreSQL",
    ]


def test_parse_resume_urls_ignores_blank_lines_and_marks_source():
    assert _parse_resume_urls(
        " https://drive.google.com/file/d/one/view\n\nhttps://example.test/two.pdf "
    ) == [
        {
            "resume_url": "https://drive.google.com/file/d/one/view",
            "source": "pasted_link",
        },
        {
            "resume_url": "https://example.test/two.pdf",
            "source": "pasted_link",
        },
    ]


def test_staff_dashboard_origin_is_allowed_for_direct_bulk_uploads():
    assert "https://admin.barabaricollective.org" in settings.ALLOWED_ORIGINS


def test_batch_requires_jd_or_required_skills_as_role_rubric():
    with pytest.raises(HTTPException) as exc_info:
        _require_role_rubric(
            job_profile_id=None,
            role_description="Data analyst",
            required_skills=[],
        )
    assert exc_info.value.status_code == 400

    _require_role_rubric(
        job_profile_id=None,
        role_description="",
        required_skills=["Python"],
    )
    _require_role_rubric(
        job_profile_id=None,
        role_description="Analyze business data and build dashboards for stakeholders.",
        required_skills=[],
    )


def test_jd_quality_flags_missing_context_and_discriminatory_proxies():
    result = analyze_job_description(
        "We need a young native English speaker with Python experience.",
        required_skills=["Python"],
    )

    assert result["level"] == "limited"
    assert "potentially_discriminatory_criteria_detected" in result["warnings"]
    assert set(result["riskyCriteria"]) == {
        "age_or_seniority_proxy",
        "nationality_or_language_proxy",
    }


def test_semantic_payload_redacts_direct_identity_but_preserves_original():
    parsed = {
        "header": {
            "fullName": "Asha Sharma",
            "email": "asha@example.test",
            "phone": "+91 99999 88888",
        },
        "skills": ["Python"],
    }
    text, semantic = redact_for_semantic_scoring(
        "Asha Sharma | asha@example.test | +91 99999 88888 | Python",
        parsed,
    )

    assert "Asha Sharma" not in text
    assert "asha@example.test" not in text
    assert semantic["header"] == {}
    assert parsed["header"]["fullName"] == "Asha Sharma"


def test_confidence_is_separate_from_score_and_exposes_interval():
    evidence = build_category_evidence(
        parsed_resume_json={
            "skills": ["Python"],
            "experience": [{"role": "Engineer", "company": "Example"}],
        },
        required_skills=["Python"],
        preferred_skills=[],
        link_report={"missing_required": []},
        jev_report={"skills_score": {"value": 4, "confidence": 0.9}},
    )
    confidence = calculate_confidence(
        role_context="job_description",
        category_evidence=evidence,
        jev_average_confidence=0.9,
        parsed_resume_json={"skills": ["Python"], "experience": [{}]},
        warnings=[],
        final_score=78.0,
    )

    assert (
        confidence["scoreInterval"]["lower"] < 78 < confidence["scoreInterval"]["upper"]
    )
    assert confidence["policy"].startswith("Confidence controls review priority")


def test_google_drive_share_link_is_converted_to_download_url():
    converted = _google_drive_download_url(
        "https://drive.google.com/file/d/file-123/view?usp=sharing"
    )

    assert converted == "https://drive.google.com/uc?export=download&id=file-123"


def test_google_drive_oauth_state_and_refresh_token_are_protected(monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(
        settings,
        "GOOGLE_DRIVE_OAUTH_REDIRECT_URI",
        "https://api.example.test/api/bulk-ats/google-drive/callback",
    )
    monkeypatch.setattr(
        settings,
        "GOOGLE_DRIVE_OAUTH_FRONTEND_RETURN_URL",
        "https://app.example.test/bulk-ats",
    )
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setattr(settings, "GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY_ID", "v1")

    authorization_url = google_drive_oauth.build_authorization_url(user_id=42)
    query = parse_qs(urlparse(authorization_url).query)
    assert query["access_type"] == ["offline"]
    assert query["scope"] == ["https://www.googleapis.com/auth/drive.readonly"]
    assert google_drive_oauth.decode_state(query["state"][0]) == 42

    encrypted = google_drive_oauth.encrypt_refresh_token("refresh-token")
    assert "refresh-token" not in encrypted
    assert (
        google_drive_oauth.decrypt_refresh_token(encrypted, key_id="v1")
        == "refresh-token"
    )
    with pytest.raises(google_drive_oauth.DriveOAuthStateError):
        google_drive_oauth.decode_state(query["state"][0] + "tampered")


@pytest.mark.asyncio
async def test_fail_closed_malware_scan_rejects_missing_scanner(monkeypatch):
    monkeypatch.setattr(settings, "BULK_ATS_CLAMAV_HOST", "")
    monkeypatch.setattr(settings, "BULK_ATS_CLAMAV_FAIL_CLOSED", True)

    result = await scan_document(b"synthetic")

    assert result == {"status": "error", "reason": "scanner_not_configured"}


def test_resume_signature_detection_rejects_html_and_accepts_pdf_docx():
    assert _detect_resume_type(b"<html>Sign in to Google Drive</html>") is None
    assert _detect_resume_type(b"%PDF-1.7\nexample") == (".pdf", "application/pdf")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "content types")
        archive.writestr("word/document.xml", "document")
    assert _detect_resume_type(buffer.getvalue()) == (
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@pytest.mark.asyncio
async def test_drive_fetch_revalidates_redirect_and_returns_detected_file(monkeypatch):
    import httpx

    requested_urls: list[str] = []

    class FakeResponse:
        def __init__(self, status_code, headers, content=b""):
            self.status_code = status_code
            self.headers = headers
            self.content = content

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def aiter_bytes(self):
            yield self.content

    responses = iter(
        [
            FakeResponse(
                302,
                {"location": "https://drive.usercontent.google.com/download/resume"},
            ),
            FakeResponse(
                200,
                {"content-disposition": 'attachment; filename="Candidate Resume.pdf"'},
                b"%PDF-1.7\nsynthetic",
            ),
        ]
    )

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, _method, url):
            requested_urls.append(url)
            return next(responses)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(worker_tasks, "_is_public_http_url", lambda _url: True)

    result = await _fetch_resume_from_url(
        "https://drive.google.com/file/d/file-123/view?usp=sharing"
    )

    assert requested_urls == [
        "https://drive.google.com/uc?export=download&id=file-123",
        "https://drive.usercontent.google.com/download/resume",
    ]
    assert result.content == b"%PDF-1.7\nsynthetic"
    assert result.filename == "Candidate Resume.pdf"
    assert result.content_type == "application/pdf"


@pytest.mark.asyncio
async def test_drive_fetch_rejects_non_file_permission_page(monkeypatch):
    import httpx

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def aiter_bytes(self):
            yield b"<html>Request access</html>"

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, _method, _url):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(worker_tasks, "_is_public_http_url", lambda _url: True)

    result = await _fetch_resume_from_url(
        "https://drive.google.com/file/d/private-file/view"
    )

    assert result.content is None
    assert "sharing permissions" in result.error


@pytest.mark.asyncio
async def test_private_drive_fetch_uses_drive_api_bearer_token(monkeypatch):
    import httpx

    requests: list[tuple[str, dict]] = []

    class FakeResponse:
        status_code = 200
        headers = {"content-disposition": 'attachment; filename="Private Resume.pdf"'}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def aiter_bytes(self):
            yield b"%PDF-1.7\nprivate"

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, _method, url, **kwargs):
            requests.append((url, kwargs.get("headers") or {}))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(worker_tasks, "_is_public_http_url", lambda _url: True)

    result = await _fetch_resume_from_url(
        "https://drive.google.com/file/d/private-file/view",
        google_access_token="short-lived-token",
    )

    assert requests == [
        (
            "https://www.googleapis.com/drive/v3/files/private-file?alt=media&supportsAllDrives=true",
            {"Authorization": "Bearer short-lived-token"},
        )
    ]
    assert result.filename == "Private Resume.pdf"


def test_bulk_ats_endpoints_are_exposed_in_openapi():
    paths = backend_app.openapi()["paths"]

    assert "/api/bulk-ats/me" in paths
    assert "/api/bulk-ats/batches" in paths
    assert "/api/bulk-ats/batches/{batch_id}/resumes" in paths
    assert "/api/bulk-ats/batches/{batch_id}/import-links" in paths
    assert "/api/bulk-ats/batches/{batch_id}/candidates" in paths
    assert "/api/bulk-ats/candidates/{candidate_id}/rerun" in paths
    assert "/api/bulk-ats/candidates/{candidate_id}/review" in paths
    assert "/api/bulk-ats/google-drive/status" in paths
    assert "/api/bulk-ats/google-drive/connect" in paths
    assert "/api/bulk-ats/google-drive/connection" in paths


@pytest.mark.asyncio
async def test_list_candidates_supports_assigned_reviewer_filter(monkeypatch):
    candidate = SimpleNamespace(id=12)
    repo = SimpleNamespace(
        list_candidates=AsyncMock(return_value=[candidate]),
        count_candidates=AsyncMock(return_value=1),
    )
    monkeypatch.setattr(bulk_ats_routes, "BulkATSCRUDRepository", lambda _session: repo)
    monkeypatch.setattr(
        bulk_ats_routes,
        "_get_authorized_batch",
        AsyncMock(return_value=SimpleNamespace(id=4)),
    )
    monkeypatch.setattr(
        bulk_ats_routes, "_candidate_summary", lambda item: {"id": item.id}
    )

    response = await bulk_ats_routes.list_candidates(
        batch_id=4,
        current_user=SimpleNamespace(id=7),
        session=SimpleNamespace(),
        assigned_reviewer_id=23,
    )

    assert response == {"total": 1, "candidates": [{"id": 12}]}
    repo.list_candidates.assert_awaited_once_with(
        batch_id=4,
        status=None,
        sort="score",
        limit=50,
        offset=0,
        assigned_reviewer_id=23,
    )
    repo.count_candidates.assert_awaited_once_with(
        batch_id=4, status=None, assigned_reviewer_id=23
    )


@pytest.mark.asyncio
async def test_rerun_returns_503_when_background_job_cannot_be_queued(monkeypatch):
    candidate = SimpleNamespace(id=28, batch_id=4)
    repo = SimpleNamespace(
        update_candidate_status=AsyncMock(),
        refresh_batch_status=AsyncMock(),
        record_audit=AsyncMock(),
    )
    monkeypatch.setattr(bulk_ats_routes, "BulkATSCRUDRepository", lambda _session: repo)
    monkeypatch.setattr(
        bulk_ats_routes,
        "_get_authorized_candidate",
        AsyncMock(return_value=candidate),
    )
    monkeypatch.setattr(bulk_ats_routes, "enqueue_job", AsyncMock(return_value=None))
    monkeypatch.setattr(
        bulk_ats_routes,
        "_mark_queue_failure",
        AsyncMock(),
    )

    with pytest.raises(HTTPException) as captured:
        await bulk_ats_routes.rerun_candidate(
            candidate_id=28,
            current_user=SimpleNamespace(id=7),
            session=SimpleNamespace(),
        )

    assert captured.value.status_code == 503
    bulk_ats_routes._mark_queue_failure.assert_awaited_once_with(
        repo, candidate_id=28, batch_id=4
    )
    repo.record_audit.assert_awaited_once()
    assert repo.record_audit.await_args.kwargs["metadata"] == {"queued": False}


@pytest.mark.asyncio
async def test_rerun_returns_job_id_after_successful_enqueue(monkeypatch):
    candidate = SimpleNamespace(id=28, batch_id=4)
    repo = SimpleNamespace(
        update_candidate_status=AsyncMock(),
        refresh_batch_status=AsyncMock(),
        record_audit=AsyncMock(),
    )
    monkeypatch.setattr(bulk_ats_routes, "BulkATSCRUDRepository", lambda _session: repo)
    monkeypatch.setattr(
        bulk_ats_routes,
        "_get_authorized_candidate",
        AsyncMock(return_value=candidate),
    )
    monkeypatch.setattr(
        bulk_ats_routes,
        "enqueue_job",
        AsyncMock(return_value="rerun-job-28"),
    )

    result = await bulk_ats_routes.rerun_candidate(
        candidate_id=28,
        current_user=SimpleNamespace(id=7),
        session=SimpleNamespace(),
    )

    assert result == {"candidateId": 28, "queued": True, "jobId": "rerun-job-28"}
    repo.refresh_batch_status.assert_awaited_once_with(4)
    repo.record_audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_stub_jev_provider_forces_manual_review():
    answers = await jev_client.StubJevProvider().evaluate()

    assert answers["needs_manual_review"].value is True
    assert answers["needs_manual_review"].confidence == 0.0
    assert answers["skills_score"].value is None


@pytest.mark.asyncio
async def test_real_jev_provider_calls_system_one_and_maps_answers(monkeypatch):
    response = SimpleNamespace(
        scores={
            key: SimpleNamespace(score=3.0, confidence=0.8)
            for key in jev_client.SCORE_LEVELS
        },
        choices={
            "education_quality": SimpleNamespace(choice="Strong", confidence=0.9),
            "project_tier": SimpleNamespace(choice="Good", confidence=0.8),
        },
        nouls={
            "needs_manual_review": SimpleNamespace(noul=0.2),
            "experience_mode_mismatch": SimpleNamespace(noul=0.1),
        },
    )
    system_one = AsyncMock(return_value=response)

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def system_one(self, **kwargs):
            return await system_one(**kwargs)

    monkeypatch.setattr(jev_client, "AsyncTypeSafeClient", FakeClient)
    provider = jev_client.JevClient(
        api_key="test-key",
        base_url=None,
        model=None,
        timeout=12,
    )

    answers = await provider.evaluate(
        resume_text="Python developer",
        parsed_resume_json={"skills": ["Python"]},
        role_name="Backend Developer",
        role_description="Build APIs",
        evaluation_intent="Assess delivery fit for a regulated fintech migration",
        required_skills=["Python"],
        preferred_skills=["FastAPI"],
    )

    assert system_one.await_count == 1
    request = system_one.await_args.kwargs
    assert request["state"]["role_name"] == "Backend Developer"
    assert request["state"]["evaluation_intent"].startswith("Assess delivery fit")
    assert (
        "score only resume-supported evidence"
        in request["state"]["evaluation_context_policy"]
    )
    assert request["questions"] is jev_client.QUESTIONS
    assert answers["skills_score"].value == 3.0
    assert answers["education_quality"].value == "Strong"
    assert answers["needs_manual_review"].value is False


@pytest.mark.asyncio
async def test_jev_transport_failure_degrades_to_manual_review(monkeypatch):
    class BrokenClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            raise TimeoutError("provider timed out")

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(jev_client, "AsyncTypeSafeClient", BrokenClient)
    provider = jev_client.JevClient(
        api_key="test-key", base_url=None, model=None, timeout=1
    )

    answers = await provider.evaluate(
        resume_text="Synthetic candidate",
        parsed_resume_json={},
        role_name="Engineer",
        role_description="Build reliable APIs",
        required_skills=["Python"],
        preferred_skills=[],
    )

    assert answers["needs_manual_review"].value is True
    assert answers["skills_score"].value is None


@pytest.mark.asyncio
async def test_empty_resume_text_routes_to_ocr_review_without_calling_jev(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "extract_resume_text",
        AsyncMock(return_value={"text": "", "documentMap": []}),
    )
    provider = SimpleNamespace(evaluate=AsyncMock())

    result = await pipeline.run_candidate_pipeline(
        raw_bytes=b"%PDF-1.7\nscan",
        filename="scan.pdf",
        role_name="Engineer",
        role_description="Build APIs",
        required_skills=["Python"],
        preferred_skills=[],
        experience_mode="experienced",
        weight_overrides=None,
        jev_provider=provider,
    )

    assert result.status == "needs_review"
    assert result.final_score is None
    assert "ocr_or_manual_review_required" in result.warnings
    provider.evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_scanned_pdf_parser_error_routes_to_manual_review(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "extract_resume_text",
        AsyncMock(
            side_effect=HTTPException(
                status_code=400, detail="No readable text found; OCR is required"
            )
        ),
    )
    provider = SimpleNamespace(evaluate=AsyncMock())

    result = await pipeline.run_candidate_pipeline(
        raw_bytes=b"%PDF-1.7\nscan",
        filename="scan.pdf",
        role_name="Engineer",
        role_description="Build APIs",
        required_skills=["Python"],
        preferred_skills=[],
        experience_mode="experienced",
        weight_overrides=None,
        jev_provider=provider,
    )

    assert result.status == "needs_review"
    assert "ocr_or_manual_review_required" in result.warnings
    provider.evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_candidate_pipeline_builds_metric_scorecard_and_brief(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "extract_resume_text",
        AsyncMock(
            return_value={
                "text": "Synthetic Python engineer resume",
                "documentMap": [],
                "embeddedLinks": [],
            }
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "generate_structured_resume_data",
        AsyncMock(
            return_value={
                "header": {
                    "fullName": "Test Candidate",
                    "email": "candidate@example.test",
                },
                "skills": ["Python"],
                "education": [],
                "projects": [],
                "experience": [],
            }
        ),
    )
    monkeypatch.setattr(pipeline, "extract_professional_urls", lambda _text: [])

    class FakeLinkValidator:
        async def validate_all_links_async(self, _targets):
            return {}

    monkeypatch.setattr(pipeline, "SmartLinkValidator", FakeLinkValidator)
    monkeypatch.setattr(
        pipeline._link_scorer,
        "score_links",
        lambda **_kwargs: {"categoryBreakdown": {}},
    )
    monkeypatch.setattr(
        pipeline,
        "check_link_requirements",
        lambda *_args, **_kwargs: {"warnings": [], "missing_required": []},
    )
    monkeypatch.setattr(
        pipeline._ats_engine,
        "run_assessment",
        lambda **_kwargs: {
            "scoreBreakdown": {
                "skillsMatch": 80,
                "educationScore": 60,
                "experienceMatch": 70,
                "projectMatch": 50,
                "linkIntegrity": 40,
            },
            "hygieneCheck": {"hasEmail": True, "hasPhone": True},
        },
    )
    answers = {
        "skills_score": jev_client.JevAnswer("skills_score", "score", 4.0, 0.9),
        "education_quality": jev_client.JevAnswer(
            "education_quality", "choice", "Strong", 0.9
        ),
        "experience_relevance": jev_client.JevAnswer(
            "experience_relevance", "score", 3.0, 0.8
        ),
        "project_quality": jev_client.JevAnswer("project_quality", "score", 2.0, 0.8),
        "project_complexity": jev_client.JevAnswer(
            "project_complexity", "score", 2.0, 0.8
        ),
        "resume_hygiene": jev_client.JevAnswer("resume_hygiene", "score", 3.0, 0.9),
        "project_tier": jev_client.JevAnswer("project_tier", "choice", "Average", 0.8),
        "needs_manual_review": jev_client.JevAnswer(
            "needs_manual_review", "noul", False, 0.1
        ),
    }
    provider = SimpleNamespace(evaluate=AsyncMock(return_value=answers))

    result = await pipeline.run_candidate_pipeline(
        raw_bytes=b"%PDF-1.7\nsynthetic",
        filename="candidate.pdf",
        role_name="Backend Engineer",
        role_description=(
            "Build and operate Python APIs, investigate production issues, and "
            "communicate technical trade-offs to product stakeholders."
        ),
        evaluation_intent="Evaluate ownership of a short fintech migration project",
        required_skills=[],
        preferred_skills=[],
        experience_mode="experienced",
        weight_overrides=None,
        jev_provider=provider,
    )

    scorecard = result.deterministic_report["bulkAtsScorecard"]
    assert set(scorecard["categoryScores"]) == {
        "skills",
        "education",
        "experience",
        "projects",
        "links",
    }
    assert (
        round(sum(scorecard["weightedContributions"].values()), 2) == result.final_score
    )
    assert result.deterministic_report["candidateBrief"]["summary"].startswith(
        "Scored "
    )
    assert scorecard["evaluationIntentApplied"] is True
    assert scorecard["roleContext"] == "job_description"
    assert scorecard["scoreApplicability"]["skills"] is True
    assert "missing_skills_rubric" not in result.warnings
    assert provider.evaluate.await_args.kwargs["evaluation_intent"].startswith(
        "Evaluate ownership"
    )
    assert result.status == "completed"


def test_candidate_brief_surfaces_strongest_and_weakest_metrics():
    brief = pipeline._build_candidate_brief(
        role_name="Backend Engineer",
        final_score=74.5,
        score_band="strong",
        category_scores={
            "skills": 90,
            "education": 70,
            "experience": 80,
            "projects": 60,
            "links": 40,
        },
        warnings=["missing_github"],
        needs_review=True,
    )

    assert [item["metric"] for item in brief["strengths"]] == [
        "Skills match",
        "Experience relevance",
    ]
    assert [item["metric"] for item in brief["gaps"]] == [
        "Links and evidence",
        "Project quality",
    ]
    assert brief["recommendation"] == "Manual review required"


def test_domain_profile_and_link_policy_do_not_make_github_a_data_requirement():
    profile = infer_domain_profile(
        role_name="Data Analyst",
        role_description="Build dashboards and explain business insights",
        required_skills=["SQL", "Tableau"],
    )
    requirements = explicit_link_requirements(
        role_description="Build dashboards and explain business insights",
        evaluation_intent="Assess analytical thinking for a retail project",
        required_skills=["SQL", "Tableau"],
    )
    report = check_link_requirements(
        verified_links={"links": {}},
        required_categories=requirements,
        domain_profile=profile,
    )

    assert profile.id == "data_analytics"
    assert requirements == ()
    assert report["scoreApplicable"] is False
    assert report["linkScore"] is None
    assert report["missing_required"] == []
    weights = resolve_weights(
        "experienced",
        None,
        applicable_categories={"skills", "education", "experience", "projects"},
    )
    assert weights["links"] == 0
    assert round(sum(weights.values()), 6) == 100


def test_explicit_required_evidence_is_scored_and_all_links_are_returned():
    profile = infer_domain_profile(role_name="Product Manager")
    requirements = explicit_link_requirements(
        role_description="A product case study link is required.",
        evaluation_intent="",
        required_skills=[],
    )
    report = check_link_requirements(
        verified_links={
            "links": {
                "https://example.test/case-study": {
                    "valid": True,
                    "status_code": 200,
                    "category": "portfolio",
                    "platform": "custom_domain",
                    "anchorText": "Launch case study",
                },
                "https://linkedin.com/in/candidate": {
                    "valid": True,
                    "status_code": 200,
                    "category": "social",
                    "platform": "linkedin",
                },
            }
        },
        required_categories=requirements,
        domain_profile=profile,
    )

    assert requirements == ("project_evidence",)
    assert report["scoreApplicable"] is True
    assert report["linkScore"] == 100
    assert report["passed"] == ["project_evidence_working"]
    assert {item["url"] for item in report["links"]} == {
        "https://example.test/case-study",
        "https://linkedin.com/in/candidate",
    }


def test_non_technical_and_data_work_sample_urls_are_extracted():
    urls = extract_professional_urls(
        "Dashboard: public.tableau.com/views/retention Portfolio: "
        "https://research.example.org/case-study Design: behance.net/candidate"
    )

    assert set(urls) == {
        "https://public.tableau.com/views/retention",
        "https://research.example.org/case-study",
        "https://behance.net/candidate",
    }


def test_candidate_link_normalization_rejects_contact_and_malformed_urls():
    validator = SmartLinkValidator()

    assert validator._normalize_candidate_url("mailto:candidate@example.test") == ""
    assert (
        validator._normalize_candidate_url("https://mailto:candidate@example.test")
        == ""
    )
    assert validator._normalize_candidate_url("candidate@example.test") == ""
    assert (
        validator._normalize_candidate_url("HTTPS://Example.Test/work/#section")
        == "https://example.test/work"
    )


@pytest.mark.asyncio
async def test_candidate_link_validation_hides_system_and_contact_links():
    validator = SmartLinkValidator()
    result = await validator.validate_all_links_async(
        [
            "mailto:candidate@example.test",
            "https://mailto:candidate@example.test",
            "https://www.google.com/search?q=candidate",
        ]
    )

    assert result["total_links_found"] == 0
    assert result["links"] == {}


def test_domain_aware_project_score_rewards_ownership_and_outcomes_without_github():
    result = ProjectScorer().score_projects(
        [
            {
                "title": "Community retention programme",
                "description": "Designed and led interviews with learners.",
                "highlights": [
                    "Created a new support journey",
                    "Coordinated delivery across three teams",
                    "Improved retention by 18%",
                ],
            }
        ],
        role_keywords=["programme", "learner", "stakeholder"],
        domain_profile={
            "id": "education_social_impact",
            "label": "Education and social impact",
        },
    )

    project = result["projectEvaluation"][0]
    assert result["scoringModel"] == "domain_aware_v1"
    assert project["score"] >= 25
    assert not any(
        "GitHub" in gap or "repository" in gap for gap in project["detectedGaps"]
    )
    assert set(project["breakdown"]) == {
        "clarity",
        "ownership",
        "roleRelevance",
        "outcomes",
        "supportingEvidence",
    }


def test_experience_fallback_does_not_treat_internships_as_professional_mismatch():
    assert not pipeline._has_sustained_professional_experience(
        {
            "experience": [
                {"title": "Data Analyst Intern"},
                {"title": "Campus Volunteer"},
            ]
        }
    )
    assert pipeline._has_sustained_professional_experience(
        {"experience": [{"title": "Data Analyst"}]}
    )


@pytest.mark.asyncio
async def test_fresher_batch_suppresses_score_for_jev_experience_mismatch(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "extract_resume_text",
        AsyncMock(
            return_value={
                "text": "Product Manager at Example, Jan 2022 - Present",
                "documentMap": [],
                "embeddedLinks": [],
            }
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "generate_structured_resume_data",
        AsyncMock(
            return_value={
                "header": {"fullName": "Candidate"},
                "skills": ["Roadmapping"],
                "education": [],
                "projects": [],
                "experience": [
                    {
                        "title": "Product Manager",
                        "company": "Example",
                        "duration": "Jan 2022 - Present",
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(pipeline, "extract_professional_urls", lambda _text: [])

    class FakeLinkValidator:
        async def validate_all_links_async(self, _targets):
            return {"links": {}}

    monkeypatch.setattr(pipeline, "SmartLinkValidator", FakeLinkValidator)
    monkeypatch.setattr(
        pipeline._link_scorer,
        "score_links",
        lambda **_kwargs: {"categoryBreakdown": {}},
    )
    monkeypatch.setattr(
        pipeline._ats_engine,
        "run_assessment",
        lambda **_kwargs: {
            "scoreBreakdown": {
                "skillsMatch": 80,
                "educationScore": 60,
                "experienceMatch": 90,
                "projectMatch": 70,
                "linkIntegrity": 0,
            },
            "hygieneCheck": {"hasEmail": True, "hasPhone": True},
            "deterministicMetrics": {
                "experienceModule": {
                    "totalMonths": 48,
                    "formattedDuration": "4 yrs",
                },
                "projectModule": {"projectEvaluation": []},
            },
        },
    )
    answers = {
        "skills_score": jev_client.JevAnswer("skills_score", "score", 3.0, 0.9),
        "needs_manual_review": jev_client.JevAnswer(
            "needs_manual_review", "noul", False, 0.1
        ),
        "experience_mode_mismatch": jev_client.JevAnswer(
            "experience_mode_mismatch", "noul", True, 0.95
        ),
    }
    provider = SimpleNamespace(evaluate=AsyncMock(return_value=answers))

    result = await pipeline.run_candidate_pipeline(
        raw_bytes=b"%PDF-1.7\nsynthetic",
        filename="candidate.pdf",
        role_name="Product Intern",
        role_description="Support product discovery",
        required_skills=["Roadmapping"],
        preferred_skills=[],
        experience_mode="fresher",
        weight_overrides=None,
        jev_provider=provider,
    )

    assert result.status == "needs_review"
    assert result.final_score is None
    gate = result.deterministic_report["bulkAtsReviewGate"]
    assert gate["detectedField"] == "Product management"
    assert gate["source"] == "jev"
    assert result.deterministic_report["bulkAtsScorecard"]["scoreSuppressed"] is True
    assert "experienced_candidate_in_fresher_batch" in result.warnings


def test_optional_job_description_reports_role_context_quality():
    warnings, context = pipeline._assess_role_context(
        role_description=(
            "Analyze business data with Excel and SQL, build dashboards, and "
            "communicate recommendations to operational stakeholders."
        ),
        evaluation_intent="",
        required_skills=[],
        preferred_skills=[],
    )
    assert context == "job_description"
    assert warnings == []

    warnings, context = pipeline._assess_role_context(
        role_description="",
        evaluation_intent="",
        required_skills=["Python"],
        preferred_skills=[],
    )
    assert context == "role_and_skills"
    assert warnings == ["missing_job_description"]

    warnings, context = pipeline._assess_role_context(
        role_description="",
        evaluation_intent="",
        required_skills=[],
        preferred_skills=[],
    )
    assert context == "role_only"
    assert warnings == [
        "missing_job_description",
        "missing_skills_rubric",
        "minimal_role_context",
    ]

    warnings, context = pipeline._assess_role_context(
        role_description="",
        evaluation_intent="Assess fit for a six-month data migration",
        required_skills=["Python"],
        preferred_skills=[],
    )
    assert context == "evaluation_intent_and_skills"
    assert warnings == ["missing_job_description"]

    warnings, context = pipeline._assess_role_context(
        role_description="",
        evaluation_intent="Best person",
        required_skills=[],
        preferred_skills=[],
    )
    assert context == "role_only"
    assert warnings == [
        "missing_job_description",
        "missing_skills_rubric",
        "evaluation_intent_too_brief",
        "minimal_role_context",
    ]


def test_skills_without_job_rubric_are_extracted_but_not_scored():
    scorer = pipeline._ats_engine.skills_scorer
    report = scorer.score_skills(
        resume_skills=["Python", "PyTorch", "Pandas"],
        jd_skills=[],
        raw_resume_text="Python PyTorch Pandas OpenCV",
        raw_jd_text="",
    )

    assert report["scoreAvailable"] is False
    assert report["totalScore"] == 0.0
    assert set(report["skillsAnalysis"]["additionalSkills"]) >= {
        "Python",
        "PyTorch",
        "Pandas",
        "OpenCV",
    }

    scorecard = pipeline._ats_engine.calculator.calculate_final_ats_score(
        link_report={"totalScore": 0},
        skills_report=report,
        education_report={"totalScore": 0},
        experience_report={"totalScore": 0, "totalMonths": 0},
        project_report={"totalScore": 0},
    )
    assert scorecard["scoreBreakdown"]["skillsMatch"] is None


def test_data_and_ai_skills_are_normalized_for_role_matching():
    report = pipeline._ats_engine.skills_scorer.score_skills(
        resume_skills=[
            "Python",
            "MySQL",
            "PyTorch",
            "Scikit-learn",
            "OpenCV",
            "Pandas",
        ],
        jd_skills=["Python", "SQL", "Machine Learning", "Power BI"],
    )

    analysis = report["skillsAnalysis"]
    assert analysis["strongSkills"] == ["Python"]
    assert "MySQL" in analysis["relatedSkills"]
    assert set(analysis["missingSkills"]) == {"Machine Learning", "Power BI"}
    assert set(analysis["additionalSkills"]) >= {
        "OpenCV",
        "Pandas",
        "PyTorch",
        "Scikit-learn",
    }


def test_deepseek_payload_includes_intent_as_context_only():
    payload = deepseek_feedback._input_payload(
        role_name="Backend Engineer",
        evaluation_intent="Prioritize evidence of migration ownership",
        scorecard={"categoryScores": {"skills": 80}, "confidence": {}},
        evidence={"skills": [{"source": "resume.skills", "text": "Python"}]},
        warnings=[],
    )

    assert payload["evaluationIntent"] == "Prioritize evidence of migration ownership"
    assert "resume" not in payload


def test_shareable_feedback_rejects_empty_model_output():
    with pytest.raises(pydantic.ValidationError):
        deepseek_feedback.ShareableFeedbackPayload.model_validate(
            {
                "summary": "",
                "strengths": [],
                "improvements": [],
                "nextSteps": [],
                "interviewFocus": [],
            }
        )


def test_feedback_states_tolerate_historical_reports():
    assert _feedback_states(None) == {"student": None, "team": None}
    assert _feedback_states(
        {"shareableFeedback": {"student": {"status": "ready"}}}
    ) == {"student": "ready", "team": None}


def test_feedback_states_turn_stale_jobs_into_terminal_failures():
    stale = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    ).isoformat()

    assert _feedback_states(
        {"shareableFeedback": {"student": {"status": "generating", "startedAt": stale}}}
    ) == {"student": "failed", "team": None}


def test_feedback_request_recovers_after_stale_or_malformed_timestamp():
    fresh = datetime.datetime.now(datetime.timezone.utc).isoformat()
    stale = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    ).isoformat()

    assert _feedback_request_is_active({"status": "generating", "startedAt": fresh})
    assert not _feedback_request_is_active({"status": "generating", "startedAt": stale})
    assert not _feedback_request_is_active(
        {"status": "queued", "requestedAt": "not-a-timestamp"}
    )


def test_feedback_copy_is_audience_appropriate():
    candidate = SimpleNamespace(candidate_name="Asha", final_score=82.5)
    content = {
        "summary": "Strong evidence for the role.",
        "strengths": ["Clear Python projects"],
        "improvements": ["Add measurable outcomes"],
        "nextSteps": ["Rewrite two project bullets"],
        "interviewFocus": ["Explain one project decision"],
    }

    student_copy = _format_feedback_copy(
        candidate=candidate,
        audience="student",
        role_name="Data Analyst",
        content=content,
    )
    team_copy = _format_feedback_copy(
        candidate=candidate,
        audience="team",
        role_name="Data Analyst",
        content=content,
    )

    assert "ATS score" not in student_copy
    assert "Improvements:" in student_copy
    assert "ATS score: 82.50/100" in team_copy
    assert "Gaps to validate:" in team_copy


@pytest.mark.asyncio
async def test_shareable_feedback_uses_deepseek_without_candidate_identity(monkeypatch):
    completion = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"summary":"Good foundation.",'
                            '"strengths":["Python evidence"],'
                            '"improvements":["Quantify outcomes"],'
                            '"nextSteps":["Update project bullets"],'
                            '"interviewFocus":["Explain project choices"]}'
                        )
                    )
                )
            ],
            usage=SimpleNamespace(prompt_tokens=120, completion_tokens=80),
        )
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=completion))
    )
    monkeypatch.setattr(deepseek_feedback, "AsyncOpenAI", lambda **_kwargs: fake_client)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "configured-for-test")
    monkeypatch.setattr(settings, "BULK_ATS_DEEPSEEK_FEEDBACK_ENABLED", True)

    content, meta = await deepseek_feedback.generate_shareable_feedback(
        audience="student",
        role_name="Data Analyst",
        evaluation_intent="Assess dashboard readiness",
        scorecard={
            "overallScore": 82.5,
            "categoryScores": {"skills": 88},
            "confidence": {"level": "high"},
        },
        evidence={"skills": [{"source": "resume.skills", "text": "Python"}]},
        warnings=["missing_job_description"],
    )

    assert content["summary"] == "Good foundation."
    assert meta["source"] == "deepseek"
    assert meta["audience"] == "student"
    request = completion.await_args.kwargs
    serialized_input = request["messages"][1]["content"]
    assert "Data Analyst" in serialized_input
    assert "Asha" not in serialized_input
    assert "example.com" not in serialized_input
    assert '"nextSteps":["..."]' in request["messages"][0]["content"]
    assert request["max_tokens"] == settings.BULK_ATS_DEEPSEEK_FEEDBACK_MAX_TOKENS


@pytest.mark.asyncio
async def test_shareable_feedback_retries_truncated_json_once(monkeypatch):
    truncated = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"summary":"Incomplete'),
                finish_reason="length",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=350),
    )
    complete = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"summary":"Good foundation.",'
                        '"strengths":["Python evidence"],'
                        '"improvements":["Quantify outcomes"],'
                        '"nextSteps":["Update project bullets"],'
                        '"interviewFocus":["Explain project choices"]}'
                    )
                ),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=110, completion_tokens=75),
    )
    completion = AsyncMock(side_effect=[truncated, complete])
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=completion))
    )
    monkeypatch.setattr(deepseek_feedback, "AsyncOpenAI", lambda **_kwargs: fake_client)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "configured-for-test")
    monkeypatch.setattr(settings, "BULK_ATS_DEEPSEEK_FEEDBACK_ENABLED", True)

    content, meta = await deepseek_feedback.generate_shareable_feedback(
        audience="student",
        role_name="Data Analyst",
        evaluation_intent="Assess dashboard readiness",
        scorecard={"overallScore": 72, "categoryScores": {"skills": 70}},
        evidence={"skills": [{"source": "resume.skills", "text": "Python"}]},
        warnings=[],
    )

    assert content["summary"] == "Good foundation."
    assert completion.await_count == 2
    retry_request = completion.await_args_list[1].kwargs
    assert (
        "previous response was empty, incomplete"
        in retry_request["messages"][0]["content"]
    )
    assert meta["usage"] == {
        "promptTokens": 210,
        "completionTokens": 425,
        "attempts": 2,
        "finishReason": "stop",
    }


@pytest.mark.asyncio
async def test_shareable_feedback_reports_invalid_json_after_bounded_retry(monkeypatch):
    invalid = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=""), finish_reason="stop")
        ],
        usage=None,
    )
    completion = AsyncMock(return_value=invalid)
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=completion))
    )
    monkeypatch.setattr(deepseek_feedback, "AsyncOpenAI", lambda **_kwargs: fake_client)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "configured-for-test")
    monkeypatch.setattr(settings, "BULK_ATS_DEEPSEEK_FEEDBACK_ENABLED", True)

    with pytest.raises(deepseek_feedback.ShareableFeedbackError) as captured:
        await deepseek_feedback.generate_shareable_feedback(
            audience="team",
            role_name="Data Analyst",
            evaluation_intent="",
            scorecard={"overallScore": 72},
            evidence={},
            warnings=[],
        )

    assert completion.await_count == 2
    assert captured.value.code == "invalid_provider_output"
    assert captured.value.retryable is True


@pytest.mark.asyncio
async def test_feedback_queue_reuses_ready_artifact_without_provider_cost():
    repo = SimpleNamespace(
        begin_feedback_request=AsyncMock(return_value=(None, "already_ready")),
        record_audit=AsyncMock(),
    )
    candidate = SimpleNamespace(
        id=12,
        batch_id=4,
        status="completed",
        deterministic_report={
            "bulkAtsScorecard": {"categoryScores": {"skills": 80}},
            "shareableFeedback": {"student": {"status": "ready"}},
        },
    )

    result = await _queue_candidate_feedback(
        repo=repo,
        candidate=candidate,
        audience="student",
        regenerate=False,
        requested_by=7,
    )

    assert result == {"candidateId": 12, "status": "already_ready"}
    repo.begin_feedback_request.assert_awaited_once()
    repo.record_audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_feedback_queue_persists_request_token_before_enqueue(monkeypatch):
    enqueue = AsyncMock(return_value="feedback-job-1")
    monkeypatch.setattr("src.api.routes.bulk_ats.enqueue_job", enqueue)
    repo = SimpleNamespace(
        begin_feedback_request=AsyncMock(),
        update_feedback_state=AsyncMock(),
        record_audit=AsyncMock(),
    )
    candidate = SimpleNamespace(
        id=13,
        batch_id=4,
        status="needs_review",
        deterministic_report={"bulkAtsScorecard": {"categoryScores": {"skills": 70}}},
    )
    repo.begin_feedback_request.return_value = (candidate, "reserved")

    result = await _queue_candidate_feedback(
        repo=repo,
        candidate=candidate,
        audience="team",
        regenerate=False,
        requested_by=7,
    )

    assert result["status"] == "queued"
    state = repo.begin_feedback_request.await_args.kwargs["state"]
    assert state["status"] == "queued"
    assert state["requestId"]
    assert enqueue.await_args.kwargs["request_id"] == state["requestId"]
    assert enqueue.await_args.kwargs["audience"] == "team"


@pytest.mark.asyncio
async def test_feedback_state_merge_preserves_other_audience():
    session = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
    repo = BulkATSCRUDRepository(session)
    candidate = SimpleNamespace(
        deterministic_report={
            "bulkAtsScorecard": {"categoryScores": {"skills": 80}},
            "shareableFeedback": {
                "student": {"status": "ready", "requestId": "student-1"},
                "team": {"status": "generating", "requestId": "team-1"},
            },
        }
    )
    repo._get_candidate_for_feedback_update = AsyncMock(return_value=candidate)

    _, updated = await repo.update_feedback_state(
        13,
        audience="team",
        state={"status": "ready", "requestId": "team-1"},
        expected_request_id="team-1",
    )

    assert updated is True
    assert candidate.deterministic_report["shareableFeedback"]["student"] == {
        "status": "ready",
        "requestId": "student-1",
    }
    assert candidate.deterministic_report["shareableFeedback"]["team"] == {
        "status": "ready",
        "requestId": "team-1",
    }


@pytest.mark.asyncio
async def test_feedback_state_rejects_superseded_worker_result():
    session = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
    repo = BulkATSCRUDRepository(session)
    candidate = SimpleNamespace(
        deterministic_report={
            "shareableFeedback": {
                "student": {"status": "generating", "requestId": "new-request"}
            }
        }
    )
    repo._get_candidate_for_feedback_update = AsyncMock(return_value=candidate)

    _, updated = await repo.update_feedback_state(
        13,
        audience="student",
        state={"status": "ready", "requestId": "old-request"},
        expected_request_id="old-request",
    )

    assert updated is False
    assert (
        candidate.deterministic_report["shareableFeedback"]["student"]["status"]
        == "generating"
    )
    session.refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({}, "draft"),
        ({"total": 3, "completed": 1, "needs_review": 0, "failed": 0}, "processing"),
        ({"total": 3, "completed": 1, "needs_review": 1, "failed": 1}, "completed"),
        ({"total": 2, "completed": 0, "needs_review": 0, "failed": 2}, "failed"),
    ],
)
async def test_refresh_batch_status_derives_terminal_state(counts, expected):
    session = SimpleNamespace(commit=AsyncMock())
    repo = BulkATSCRUDRepository(session)
    batch = SimpleNamespace(status="processing")
    repo.get_batch = AsyncMock(return_value=batch)

    async def count_candidates(*, batch_id, status=None):
        assert batch_id == 9
        return counts.get(status or "total", 0)

    repo.count_candidates = count_candidates

    status = await repo.refresh_batch_status(9)

    assert status == expected
    assert batch.status == expected
    if expected == "processing":
        session.commit.assert_not_awaited()
    else:
        session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recruiter_cannot_read_another_recruiters_batch():
    repo = SimpleNamespace(
        get_batch=AsyncMock(return_value=SimpleNamespace(id=7, created_by=22))
    )
    recruiter = SimpleNamespace(
        id=11,
        email="recruiter@example.com",
        role="recruiter",
        is_admin=False,
        is_super_admin=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await _get_authorized_batch(repo, batch_id=7, current_user=recruiter)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_admin_can_read_any_recruiters_candidate():
    candidate = SimpleNamespace(id=5, batch=SimpleNamespace(created_by=22))
    repo = SimpleNamespace(get_candidate=AsyncMock(return_value=candidate))
    admin = SimpleNamespace(
        id=1,
        email="admin@example.com",
        role="admin",
        is_admin=True,
        is_super_admin=False,
    )

    result = await _get_authorized_candidate(repo, candidate_id=5, current_user=admin)

    assert result is candidate
