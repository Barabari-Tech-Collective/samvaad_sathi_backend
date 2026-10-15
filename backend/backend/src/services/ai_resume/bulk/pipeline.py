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

from src.services.ai_resume.bulk.jev_client import (
    JevProvider,
    JevAnswer,
    score_answer_to_pct,
)
from src.services.ai_resume.bulk.evidence import (
    build_category_evidence,
    calculate_confidence,
    redact_for_semantic_scoring,
)
from src.services.ai_resume.bulk.link_requirements import (
    infer_role_track,
    check_link_requirements,
)
from src.services.ai_resume.bulk.jd_quality import analyze_job_description
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
    return UploadFile(
        file=io.BytesIO(raw_bytes),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


def _jev_value(answers: dict[str, JevAnswer], question_id: str) -> Any:
    answer = answers.get(question_id)
    return answer.value if answer else None


def _assess_role_context(
    *,
    role_description: str,
    evaluation_intent: str,
    required_skills: list[str],
    preferred_skills: list[str],
) -> tuple[list[str], str]:
    """Describe rubric completeness without making the optional JD mandatory."""
    warnings: list[str] = []
    has_description = bool(role_description.strip())
    normalized_intent = evaluation_intent.strip()
    has_intent = bool(normalized_intent)
    has_useful_intent = (
        len(normalized_intent) >= 30 and len(normalized_intent.split()) >= 5
    )
    has_skills = bool(required_skills or preferred_skills)
    if not has_description:
        warnings.append("missing_job_description")
    if not has_skills:
        warnings.append("missing_skills_rubric")
    if has_intent and not has_useful_intent:
        warnings.append("evaluation_intent_too_brief")
    if has_description:
        return warnings, "job_description"
    if has_useful_intent and has_skills:
        return warnings, "evaluation_intent_and_skills"
    if has_useful_intent:
        return warnings, "evaluation_intent"
    if has_skills:
        return warnings, "role_and_skills"
    warnings.append("minimal_role_context")
    return warnings, "role_only"


def _build_candidate_brief(
    *,
    role_name: str,
    final_score: float,
    score_band: str,
    category_scores: dict[str, float],
    warnings: list[str],
    needs_review: bool,
) -> dict:
    labels = {
        "skills": "Skills match",
        "education": "Education",
        "experience": "Experience relevance",
        "projects": "Project quality",
        "links": "Links and evidence",
    }
    ranked = sorted(category_scores.items(), key=lambda item: item[1], reverse=True)
    strengths = [
        {"metric": labels[key], "score": round(score, 2)} for key, score in ranked[:2]
    ]
    gaps = [
        {"metric": labels[key], "score": round(score, 2)}
        for key, score in sorted(category_scores.items(), key=lambda item: item[1])[:2]
    ]
    review_text = (
        " Manual review is required before any hiring decision."
        if needs_review
        else " The result is ready for recruiter review."
    )
    return {
        "summary": f"Scored {final_score:.1f}/100 ({score_band}) for {role_name}.{review_text}",
        "strengths": strengths,
        "gaps": gaps,
        "warnings": warnings,
        "recommendation": (
            "Manual review required"
            if needs_review
            else "Review evidence before shortlisting"
        ),
    }


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
    evaluation_intent: str = "",
) -> CandidateResult:
    warnings, role_context = _assess_role_context(
        role_description=role_description,
        evaluation_intent=evaluation_intent,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
    )

    # 1. Parse (reuses the exact single-resume extraction/structuring path).
    try:
        upload_file = _as_upload_file(raw_bytes, filename)
        parser_payload = await extract_resume_text(upload_file)
    except Exception as exc:
        logger.warning("Bulk ATS parse failed for %s: %s", filename, exc)
        detail = str(getattr(exc, "detail", exc))
        needs_ocr_review = (
            "no readable text" in detail.casefold() or "ocr" in detail.casefold()
        )
        if needs_ocr_review:
            warnings.extend(["empty_resume_text", "ocr_or_manual_review_required"])
        return CandidateResult(
            status="needs_review" if needs_ocr_review else "failed",
            error_message=f"Could not parse resume: {detail}",
            extracted_resume_text=None,
            parsed_resume_json=None,
            deterministic_report=None,
            jev_report=None,
            link_report=None,
            final_score=None,
            score_band=None,
            needs_review=True,
            warnings=sorted(set(warnings)),
        )

    if isinstance(parser_payload, dict):
        resume_text = parser_payload.get("text", "")
        document_map = parser_payload.get("documentMap", [])
        embedded_links = parser_payload.get("embeddedLinks", [])
    else:
        resume_text = str(parser_payload)
        document_map, embedded_links = [], []

    if not resume_text.strip():
        warnings.extend(["empty_resume_text", "ocr_or_manual_review_required"])
        return CandidateResult(
            status="needs_review",
            error_message=(
                "No machine-readable resume text was found. "
                "Use an OCR-enabled copy or review the document manually."
            ),
            extracted_resume_text=resume_text,
            parsed_resume_json=None,
            deterministic_report=None,
            jev_report=None,
            link_report=None,
            final_score=None,
            score_band=None,
            needs_review=True,
            warnings=warnings,
        )

    try:
        parsed_resume_json = await generate_structured_resume_data(
            resume_text=resume_text, analysis_result={}
        )
    except Exception as exc:
        logger.warning("Bulk ATS structuring failed for %s: %s", filename, exc)
        return CandidateResult(
            status="failed",
            error_message=f"Could not structure resume: {exc}",
            extracted_resume_text=resume_text,
            parsed_resume_json=None,
            deterministic_report=None,
            jev_report=None,
            link_report=None,
            final_score=None,
            score_band=None,
            needs_review=True,
            warnings=warnings,
        )

    # 2. Link validation + role-track requirement check (deterministic).
    extracted_targets = list(
        dict.fromkeys(list(embedded_links) + extract_professional_urls(resume_text))
    )
    link_validator = SmartLinkValidator()
    verified_links = await link_validator.validate_all_links_async(extracted_targets)

    role_track = infer_role_track(role_name)
    link_score_report = _link_scorer.score_links(
        validator_output=verified_links, track=role_track, job_title=role_name
    )
    link_requirement_report = check_link_requirements(
        link_score_report.get("categoryBreakdown", {}), role_track
    )
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
        logger.warning(
            "Bulk ATS deterministic scoring failed for %s: %s", filename, exc
        )
        return CandidateResult(
            status="failed",
            error_message=f"Deterministic scoring failed: {exc}",
            extracted_resume_text=resume_text,
            parsed_resume_json=parsed_resume_json,
            deterministic_report=None,
            jev_report=None,
            link_report=link_requirement_report,
            final_score=None,
            score_band=None,
            needs_review=True,
            warnings=warnings,
        )

    hygiene = deterministic_report.get("hygieneCheck", {})
    if not hygiene.get("hasEmail"):
        warnings.append("missing_email")
    if not hygiene.get("hasPhone"):
        warnings.append("missing_phone")

    # 4. Jev semantic judgment (skills/education/experience/project/hygiene
    # quality calls that are not deterministically checkable).
    semantic_text, semantic_resume_json = redact_for_semantic_scoring(
        resume_text, parsed_resume_json
    )
    jev_answers = await jev_provider.evaluate(
        resume_text=semantic_text,
        parsed_resume_json=semantic_resume_json,
        role_name=role_name,
        role_description=role_description,
        evaluation_intent=evaluation_intent,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
    )
    jev_report = {
        qid: {"value": a.value, "confidence": a.confidence, "type": a.type}
        for qid, a in jev_answers.items()
    }
    jev_needs_review = bool(_jev_value(jev_answers, "needs_manual_review"))
    # Noul's "confidence" field above is repurposed to carry its yes-probability
    # (see jev_client.JevClient.evaluate), which is not a calibration-confidence
    # in the same sense as Score/Choice - excluded here so one ambiguous Noul
    # answer can't dominate the average.
    jev_confidences = [
        a.confidence
        for a in jev_answers.values()
        if a.confidence is not None and a.type != "noul"
    ]
    low_jev_confidence = (
        bool(jev_confidences) and (sum(jev_confidences) / len(jev_confidences)) < 0.5
    )
    jev_average_confidence = (
        (sum(jev_confidences) / len(jev_confidences)) if jev_confidences else None
    )
    jev_scored = any(
        answer.type in {"score", "choice"} and answer.value is not None
        for answer in jev_answers.values()
    )
    if not jev_scored:
        warnings.append("jev_unavailable")
    if low_jev_confidence:
        warnings.append("low_jev_confidence")

    # 5. Weighted final score. ATSCalculator's scoreBreakdown (skillsMatch,
    # experienceMatch, projectMatch, linkIntegrity, educationScore) is
    # already 0-100, so each category blends that directly with Jev's
    # matching score-type answer; Jev being unavailable (stub provider, or a
    # failed call) degrades the category to the deterministic number alone
    # rather than failing the candidate.
    deterministic_breakdown = deterministic_report.get("scoreBreakdown", {})

    EDUCATION_QUALITY_SCORE = {
        "Strong": 100.0,
        "Adequate": 70.0,
        "Weak": 40.0,
        "Missing": 0.0,
    }

    def blended(deterministic_pct_key: str, jev_pct: float | None) -> float:
        det_pct = deterministic_breakdown.get(deterministic_pct_key)
        det_pct = float(det_pct) if isinstance(det_pct, (int, float)) else None
        if det_pct is not None and jev_pct is not None:
            return (det_pct + jev_pct) / 2
        return (
            det_pct
            if det_pct is not None
            else (jev_pct if jev_pct is not None else 0.0)
        )

    education_quality_choice = _jev_value(jev_answers, "education_quality")
    education_jev_pct = (
        EDUCATION_QUALITY_SCORE.get(education_quality_choice)
        if isinstance(education_quality_choice, str)
        else None
    )

    link_integrity_pct = deterministic_breakdown.get("linkIntegrity")
    link_integrity_pct = (
        float(link_integrity_pct)
        if isinstance(link_integrity_pct, (int, float))
        else 0.0
    )
    link_penalty = 50.0 * len(link_requirement_report["missing_required"])
    link_pct = (
        max(0.0, link_integrity_pct - link_penalty)
        if link_requirement_report["missing_required"]
        else link_integrity_pct
    )

    def jev_score_pct(question_id: str) -> float | None:
        answer = jev_answers.get(question_id)
        return score_answer_to_pct(answer) if answer else None

    category_pct = {
        "skills": blended("skillsMatch", jev_score_pct("skills_score")),
        "education": blended("educationScore", education_jev_pct),
        "experience": blended("experienceMatch", jev_score_pct("experience_relevance")),
        "projects": blended("projectMatch", jev_score_pct("project_quality")),
        "links": link_pct,
    }

    weights = resolve_weights(experience_mode, weight_overrides)
    final_score = sum(
        category_pct.get(key, 0.0) * (weights.get(key, 0.0) / 100.0) for key in weights
    )
    final_score = round(max(0.0, min(100.0, final_score)), 2)
    score_band = score_band_for(final_score)

    # Every metric carries resume/validator/provider evidence. Confidence is
    # separately calculated and never modifies the factual weighted score.
    category_evidence = build_category_evidence(
        parsed_resume_json=parsed_resume_json,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        link_report=link_requirement_report,
        jev_report=jev_report,
    )
    confidence = calculate_confidence(
        role_context=role_context,
        category_evidence=category_evidence,
        jev_average_confidence=jev_average_confidence,
        parsed_resume_json=parsed_resume_json,
        warnings=warnings,
        final_score=final_score,
        jd_quality_score=analyze_job_description(
            role_description,
            required_skills=required_skills,
            preferred_skills=preferred_skills,
        )["score"],
    )
    if confidence["level"] == "low":
        warnings.append("low_overall_confidence")

    needs_review = (
        jev_needs_review
        or low_jev_confidence
        or confidence["level"] == "low"
        or bool(link_requirement_report["missing_required"])
        or role_context == "role_only"
        or not resume_text.strip()
    )
    status = "needs_review" if needs_review else "completed"
    warnings = sorted(set(warnings))

    weighted_contributions = {
        key: round(category_pct.get(key, 0.0) * (weights.get(key, 0.0) / 100.0), 2)
        for key in weights
    }
    deterministic_report = dict(deterministic_report)
    deterministic_report["bulkAtsScorecard"] = {
        "categoryScores": {key: round(value, 2) for key, value in category_pct.items()},
        "weights": weights,
        "weightedContributions": weighted_contributions,
        "experienceMode": experience_mode,
        "roleContext": role_context,
        "evaluationIntentApplied": bool(evaluation_intent.strip()),
        "scoringSources": (
            "deterministic_and_jev" if jev_scored else "deterministic_only"
        ),
        "jevAverageConfidence": (
            round(jev_average_confidence, 4)
            if jev_average_confidence is not None
            else None
        ),
        "evidence": category_evidence,
        "confidence": confidence,
    }
    deterministic_report["candidateBrief"] = _build_candidate_brief(
        role_name=role_name,
        final_score=final_score,
        score_band=score_band,
        category_scores=category_pct,
        warnings=warnings,
        needs_review=needs_review,
    )

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
        warnings=warnings,
    )
