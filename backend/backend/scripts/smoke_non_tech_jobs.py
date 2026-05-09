"""Smoke test for global job profiles + non-tech interview generation flow."""

from __future__ import annotations

import json
import random
import string
from pathlib import Path

import httpx

from scripts.smoke_utils import (
    API,
    BASE_URL,
    auth_headers,
    extract_token,
    print_result,
    safe_call,
    safe_json,
)
from src.services.non_tech_blueprint import build_non_tech_question_bank, non_tech_category_keys


HR_COMMUNICATIONS_JOB_NAME = "HR and Communications Interview"
EXPECTED_CATEGORIES = {"self", "behavioral", "productivity", "company_candidate", "general"}
AUDIO_SAMPLE = Path("assets/Speech.mp3")


def rand_email() -> str:
    token = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"nontech_{token}@example.com"


def main() -> None:
    email = rand_email()
    password = "pass123!"
    name = "Non Tech Smoke User"

    question_bank = build_non_tech_question_bank(role_name=HR_COMMUNICATIONS_JOB_NAME, company_name="Amazon")
    for category in non_tech_category_keys():
        if len(question_bank.get(category, [])) < 20:
            raise SystemExit(f"Expected at least 20 non-tech blueprint questions for category '{category}'")

    with httpx.Client(base_url=BASE_URL, timeout=120.0) as client:
        r, err = safe_call(client, "POST", f"{API}/users", json={"email": email, "password": password, "name": name})
        print_result("POST /api/users", r, err)
        token = extract_token(safe_json(r) if r else {}) if not err else None

        r, err = safe_call(client, "POST", f"{API}/login", json={"email": email, "password": password})
        print_result("POST /api/login", r, err)
        login_body = safe_json(r) if r else {}
        token = extract_token(login_body) or token
        headers = auth_headers(token)

        resume_files = {"file": ("sample.txt", b"non tech smoke resume: stakeholder communication leadership", "text/plain")}
        r, err = safe_call(client, "POST", f"{API}/extract-resume", headers=headers, files=resume_files)
        print_result("POST /api/extract-resume", r, err)

        create_payload = {
            "jobName": HR_COMMUNICATIONS_JOB_NAME,
            "jobDescription": (
                "Assess fresher HR, communications, customer-service readiness, workplace professionalism, "
                "self-awareness, collaboration, productivity, and spoken English communication."
            ),
            "companyName": "Amazon",
            "experienceLevel": "fresher",
            "skills": ["communication", "professionalism", "teamwork", "time-management", "customer-service"],
            "additionalContext": "Focus on HR and English communication interview prompts.",
        }
        r, err = safe_call(client, "POST", f"{API}/v2/job-profiles", headers=headers, json=create_payload)
        print_result("POST /api/v2/job-profiles", r, err)
        created = safe_json(r) if r else {}
        job_profile_id = created.get("jobProfileId") or created.get("job_profile_id")
        if not job_profile_id:
            raise SystemExit("Failed to create job profile")

        r, err = safe_call(client, "GET", f"{API}/v2/job-profiles", headers=headers)
        print_result("GET /api/v2/job-profiles", r, err)
        listed = safe_json(r) if r else {}
        items = listed.get("items", []) if isinstance(listed, dict) else []
        if not any((item.get("jobProfileId") or item.get("job_profile_id")) == job_profile_id for item in items):
            raise SystemExit("Created job profile not found in list endpoint")

        gen_payload = {
            "jobProfileId": job_profile_id,
            "difficulty": "hard",
            "useResume": True,
        }
        r, err = safe_call(client, "POST", f"{API}/v2/interviews/non-tech/generate-questions", headers=headers, json=gen_payload)
        print_result("POST /api/v2/interviews/non-tech/generate-questions", r, err)
        gen = safe_json(r) if r else {}
        q_items = gen.get("items", []) if isinstance(gen, dict) else []
        if len(q_items) != 5:
            raise SystemExit(f"Expected 5 generated questions, got {len(q_items)}")

        if (gen.get("track") or "").startswith("Non-Tech:") is False:
            raise SystemExit(f"Expected non-tech track, got: {gen.get('track')}")

        item_difficulties = {str(item.get("difficulty") or "").lower() for item in q_items}
        if item_difficulties != {"medium"}:
            raise SystemExit(f"Expected fixed medium difficulty for non-tech flow, got: {sorted(item_difficulties)}")

        categories = [str(q.get("category") or "") for q in q_items]
        if set(categories) != EXPECTED_CATEGORIES:
            raise SystemExit(f"Expected one question per blueprint category, got: {categories}")
        if len(categories) != len(set(categories)):
            raise SystemExit(f"Expected unique category coverage, got duplicates: {categories}")
        if any(item.get("supplement") for item in q_items):
            raise SystemExit("Expected no supplements for non-tech interviews")

        follow_up_ready = [q for q in q_items if q.get("followUpStrategy") or q.get("follow_up_strategy")]
        if len(follow_up_ready) < 2:
            raise SystemExit("Expected at least 2 follow-up enabled questions in non-tech flow")

        print(json.dumps({
            "name": "non-tech-generation-check",
            "question_count": len(q_items),
            "categories": categories,
            "item_difficulties": sorted(item_difficulties),
            "job_name": HR_COMMUNICATIONS_JOB_NAME,
            "follow_up_ready": len(follow_up_ready),
            "interview_id": gen.get("interviewId") or gen.get("interview_id"),
            "track": gen.get("track"),
        }))

        interview_id = gen.get("interviewId") or gen.get("interview_id")
        question_id = q_items[0].get("interviewQuestionId") or q_items[0].get("interview_question_id")
        if not interview_id or not question_id:
            raise SystemExit("Generated non-tech response missing interview/question ids")
        if not AUDIO_SAMPLE.exists():
            raise SystemExit(f"Audio sample not found: {AUDIO_SAMPLE}")

        attempt_payload = {"interviewId": interview_id, "questionId": question_id}
        r, err = safe_call(client, "POST", f"{API}/interviews/question-attempts", headers=headers, json=attempt_payload)
        print_result("POST /api/interviews/question-attempts (non-tech HR)", r, err)
        attempt_body = safe_json(r) if r else {}
        question_attempt_id = attempt_body.get("questionAttemptId") or attempt_body.get("question_attempt_id")
        if not question_attempt_id:
            raise SystemExit("Failed to create question attempt for HR non-tech interview")

        with AUDIO_SAMPLE.open("rb") as audio_file:
            files = {"file": (AUDIO_SAMPLE.name, audio_file, "audio/mpeg")}
            data = {"question_attempt_id": str(question_attempt_id), "language": "en"}
            r, err = safe_call(client, "POST", f"{API}/transcribe-whisper", headers=headers, data=data, files=files)
        print_result("POST /api/transcribe-whisper (non-tech HR)", r, err)
        transcription_body = safe_json(r) if r else {}
        transcription = transcription_body.get("transcription") if isinstance(transcription_body, dict) else None
        transcription_text = transcription.get("text") if isinstance(transcription, dict) else None
        if not transcription_text:
            raise SystemExit("Transcription missing for HR non-tech answer")

        analysis_payload = {
            "question_attempt_id": question_attempt_id,
            "analysis_types": ["domain", "communication", "pace", "pause"],
        }
        r, err = safe_call(client, "POST", f"{API}/complete-analysis", headers=headers, json=analysis_payload)
        print_result("POST /api/complete-analysis (non-tech HR)", r, err)
        analysis_body = safe_json(r) if r else {}
        analysis_complete = bool(analysis_body.get("analysisComplete") or analysis_body.get("analysis_complete"))
        aggregated = analysis_body.get("aggregatedAnalysis") or analysis_body.get("aggregated_analysis") or {}
        communication = aggregated.get("communication") or {}
        pace = aggregated.get("pace") or {}
        pause = aggregated.get("pause") or {}
        if not analysis_complete:
            raise SystemExit(f"Analysis did not complete for HR non-tech answer: {analysis_body}")
        if not any(communication.get(key) is not None for key in ("communicationScore", "communication_score", "overall_score")):
            raise SystemExit("Communication analysis missing from HR non-tech answer")
        if not pace or not pause:
            raise SystemExit("Pace/pause analysis missing from HR non-tech answer")

        r, err = safe_call(client, "POST", f"{API}/interviews/complete", headers=headers, json={"interviewId": interview_id})
        print_result("POST /api/interviews/complete (non-tech HR)", r, err)
        complete_body = safe_json(r) if r else {}
        if r is None or r.status_code != 200:
            raise SystemExit(f"Failed to complete HR non-tech interview: {complete_body}")

        r, err = safe_call(client, "POST", f"{API}/summary-report", headers=headers, json={"interviewId": interview_id})
        print_result("POST /api/summary-report (non-tech HR)", r, err)
        report_body = safe_json(r) if r else {}
        score_summary = report_body.get("scoreSummary") or report_body.get("score_summary") or {}
        speech = score_summary.get("speechAndStructure") or score_summary.get("speech_and_structure") or {}
        if score_summary.get("knowledgeCompetence") is not None or score_summary.get("knowledge_competence") is not None:
            raise SystemExit("Expected non-tech summary report to omit knowledge competence score")
        if r is None or r.status_code != 200 or speech.get("score") is None:
            raise SystemExit(f"HR non-tech summary report missing speech score: {report_body}")

        r, err = safe_call(client, "POST", f"{API}/v2/summary-report", headers=headers, json={"interviewId": interview_id})
        print_result("POST /api/v2/summary-report (non-tech HR)", r, err)
        report_v2_body = safe_json(r) if r else {}
        report_v2_summary = report_v2_body.get("scoreSummary") or report_v2_body.get("score_summary") or {}
        report_v2_speech = report_v2_summary.get("speechAndStructure") or report_v2_summary.get("speech_and_structure") or {}
        if report_v2_summary.get("knowledgeCompetence") is not None or report_v2_summary.get("knowledge_competence") is not None:
            raise SystemExit("Expected non-tech V2 summary report to omit knowledge competence score")
        if r is None or r.status_code != 200 or report_v2_speech.get("score") is None:
            raise SystemExit(f"HR non-tech V2 summary report missing speech score: {report_v2_body}")

        print(json.dumps({
            "name": "non-tech-answer-report-check",
            "interview_id": interview_id,
            "question_attempt_id": question_attempt_id,
            "analysis_complete": analysis_complete,
            "summary_speech_score": speech.get("score"),
            "summary_v2_speech_score": report_v2_speech.get("score"),
        }))

        r, err = safe_call(client, "DELETE", f"{API}/v2/job-profiles/{job_profile_id}", headers=headers)
        print_result("DELETE /api/v2/job-profiles/{id}", r, err)
        deleted = safe_json(r) if r else {}
        if not deleted.get("deleted"):
            raise SystemExit("Delete endpoint did not report deletion")


if __name__ == "__main__":
    main()
