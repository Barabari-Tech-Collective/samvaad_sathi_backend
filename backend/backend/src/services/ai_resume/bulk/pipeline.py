"""Per-candidate Bulk ATS pipeline: parse -> deterministic score -> Jev
semantic score -> weighted final score -> needs_review.

Reuses the existing single-resume building blocks (parser_service,
template_service, ATSEngine, SmartLinkValidator) rather than re-implementing
them, so bulk and single-resume ATS stay consistent and any future bugfix to
those modules applies to both.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import UploadFile
from starlette.datastructures import Headers

from src.services.ai_resume.parser_service import extract_resume_text
from src.services.ai_resume.template_service import generate_structured_resume_data
from src.services.ai_resume.scoring.ats_engine import ATSEngine
from src.services.ai_resume.scoring.link_scorer import LinkScorer
from src.utilities.link_validator import SmartLinkValidator
from src.services.ai_resume.url_validator import extract_professional_urls

from src.services.ai_resume.bulk.jev_client import JevProvider, JevAnswer
from src.services.ai_resume.bulk.link_requirements import infer_role_track, check_link_requirements
from src.services.ai_resume.bulk.scoring_weights import resolve_weights, score_band_for

logger = logging.getLogger(__name__)

_ats_engine = ATSEngine()
_link_scorer = LinkScorer()

CONTENT_TYPE_BY_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass
class CandidateResult:
    status: str  # "completed" | "needs_review" | "failed"
    error_message: str | None
    extracted_resume_text: str | None
    parsed_resume_json: dict | None
    deterministic_report: dict | None
    jev_report: dict | None
    link_report: dict | None
    final_score: float | None
    score_band: str | None
    needs_review: bool
    warnings: list[str]


def file_sha256(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def _as_upload_file(raw_bytes: bytes, filename: str) -> UploadFile:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    content_type = CONTENT_TYPE_BY_EXT.get(ext, "application/octet-stream")
    return UploadFile(file=io.BytesIO(raw_bytes), filename=filename, headers=Headers({"content-type": content_type}))


def _jev_value(answers: dict[str, JevAnswer], question_id: str) -> Any:
    answer = answers.get(question_id)
    return answer.value if answer else None


async def run_candidate_pipeline(
    *,
    raw_bytes: bytes,
    filename: str,
    role_name: str,
    role_description: str,
    required_skills: list[str],
    preferred_skills: list[str],
    experience_mode: str,
    weight_overrides: dict | None,
    jev_provider: JevProvider,
) -> CandidateResult:
    warnings: list[str] = []

    # 1. Parse (reuses the exact single-resume extraction/structuring path).
    try:
        upload_file = _as_upload_file(raw_bytes, filename)
        parser_payload = await extract_resume_text(upload_file)
    except Exception as exc:
        logger.warning("Bulk ATS parse failed for %s: %s", filename, exc)
        return CandidateResult(
            status="failed", error_message=f"Could not parse resume: {exc}",
            extracted_resume_text=None, parsed_resume_json=None, deterministic_report=None,
            jev_report=None, link_report=None, final_score=None, score_band=None,
            needs_review=True, warnings=warnings,
        )

    if isinstance(parser_payload, dict):
        resume_text = parser_payload.get("text", "")
        document_map = parser_payload.get("documentMap", [])
        embedded_links = parser_payload.get("embeddedLinks", [])
    else:
        resume_text = str(parser_payload)
        document_map, embedded_links = [], []

    if not resume_text.strip():
        warnings.append("empty_resume_text")

    try:
        parsed_resume_json = await generate_structured_resume_data(resume_text=resume_text, analysis_result={})
    except Exception as exc:
        logger.warning("Bulk ATS structuring failed for %s: %s", filename, exc)
        return CandidateResult(
            status="failed", error_message=f"Could not structure resume: {exc}",
            extracted_resume_text=resume_text, parsed_resume_json=None, deterministic_report=None,
            jev_report=None, link_report=None, final_score=None, score_band=None,
            needs_review=True, warnings=warnings,
        )

    # 2. Link validation + role-track requirement check (deterministic).
    extracted_targets = list(dict.fromkeys(list(embedded_links) + extract_professional_urls(resume_text)))
    link_validator = SmartLinkValidator()
    verified_links = await link_validator.validate_all_links_async(extracted_targets)

    role_track = infer_role_track(role_name)
    link_score_report = _link_scorer.score_links(validator_output=verified_links, track=role_track, job_title=role_name)
    link_requirement_report = check_link_requirements(link_score_report.get("categoryBreakdown", {}), role_track)
    warnings.extend(link_requirement_report["warnings"])
    if link_requirement_report["missing_required"]:
        warnings.extend(link_requirement_report["missing_required"])

    # 3. Deterministic scoring (reuses the single-resume ATSEngine, using the
    # role profile's description/skills in place of a single job description).
    try:
        deterministic_report = _ats_engine.run_assessment(
            verified_links_raw=verified_links,
            parsed_skills=parsed_resume_json.get("skills", []),
            jd_skills=required_skills + preferred_skills,
            parsed_education=parsed_resume_json.get("education", []),
            parsed_projects=parsed_resume_json.get("projects", []),
            parsed_experience=parsed_resume_json.get("experience", []),
            experience_level=experience_mode,
            target_role=role_name,
            raw_resume_text=resume_text,
            raw_jd_text=role_description,
        )
    except Exception as exc:
        logger.warning("Bulk ATS deterministic scoring failed for %s: %s", filename, exc)
        return CandidateResult(
            status="failed", error_message=f"Deterministic scoring failed: {exc}",
            extracted_resume_text=resume_text, parsed_resume_json=parsed_resume_json, deterministic_report=None,
            jev_report=None, link_report=link_requirement_report, final_score=None, score_band=None,
            needs_review=True, warnings=warnings,
        )

    hygiene = deterministic_report.get("hygieneCheck", {})
    if not hygiene.get("hasEmail"):
        warnings.append("missing_email")
    if not hygiene.get("hasPhone"):
        warnings.append("missing_phone")

    # 4. Jev semantic judgment (skills/education/experience/project/hygiene
    # quality calls that are not deterministically checkable).
    jev_answers = await jev_provider.evaluate(
        resume_text=resume_text,
        parsed_resume_json=parsed_resume_json,
        role_name=role_name,
        role_description=role_description,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
    )
    jev_report = {qid: {"value": a.value, "confidence": a.confidence, "type": a.type} for qid, a in jev_answers.items()}
    jev_needs_review = bool(_jev_value(jev_answers, "needs_manual_review"))
    jev_confidences = [a.confidence for a in jev_answers.values() if a.confidence is not None]
    low_jev_confidence = bool(jev_confidences) and (sum(jev_confidences) / len(jev_confidences)) < 0.5

    # 5. Weighted final score. ATSCalculator's scoreBreakdown (skillsMatch,
    # experienceMatch, projectMatch, linkIntegrity, educationScore) is
    # already 0-100, so each category blends that directly with Jev's
    # matching score-type answer; Jev being unavailable (stub provider, or a
    # failed call) degrades the category to the deterministic number alone
    # rather than failing the candidate.
    deterministic_breakdown = deterministic_report.get("scoreBreakdown", {})

    EDUCATION_QUALITY_SCORE = {"Strong": 100.0, "Adequate": 70.0, "Weak": 40.0, "Missing": 0.0}

    def blended(deterministic_pct_key: str, jev_pct: float | None) -> float:
        det_pct = deterministic_breakdown.get(deterministic_pct_key)
        det_pct = float(det_pct) if isinstance(det_pct, (int, float)) else None
        if det_pct is not None and jev_pct is not None:
            return (det_pct + jev_pct) / 2
        return det_pct if det_pct is not None else (jev_pct if jev_pct is not None else 0.0)

    education_quality_choice = _jev_value(jev_answers, "education_quality")
    education_jev_pct = EDUCATION_QUALITY_SCORE.get(education_quality_choice) if isinstance(education_quality_choice, str) else None

    link_integrity_pct = deterministic_breakdown.get("linkIntegrity")
    link_integrity_pct = float(link_integrity_pct) if isinstance(link_integrity_pct, (int, float)) else 0.0
    link_penalty = 50.0 * len(link_requirement_report["missing_required"])
    link_pct = max(0.0, link_integrity_pct - link_penalty) if link_requirement_report["missing_required"] else link_integrity_pct

    category_pct = {
        "skills": blended("skillsMatch", _jev_value(jev_answers, "skills_score")),
        "education": blended("educationScore", education_jev_pct),
        "experience": blended("experienceMatch", _jev_value(jev_answers, "experience_relevance")),
        "projects": blended("projectMatch", _jev_value(jev_answers, "project_quality")),
        "links": link_pct,
    }

    weights = resolve_weights(experience_mode, weight_overrides)
    final_score = sum(category_pct.get(key, 0.0) * (weights.get(key, 0.0) / 100.0) for key in weights)
    final_score = round(max(0.0, min(100.0, final_score)), 2)
    score_band = score_band_for(final_score)

    needs_review = jev_needs_review or low_jev_confidence or bool(link_requirement_report["missing_required"]) or not resume_text.strip()
    status = "needs_review" if needs_review else "completed"

    return CandidateResult(
        status=status,
        error_message=None,
        extracted_resume_text=resume_text,
        parsed_resume_json=parsed_resume_json,
        deterministic_report=deterministic_report,
        jev_report=jev_report,
        link_report=link_requirement_report,
        final_score=final_score,
        score_band=score_band,
        needs_review=needs_review,
        warnings=sorted(set(warnings)),
    )
