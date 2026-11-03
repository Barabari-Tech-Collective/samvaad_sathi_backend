"""Evidence extraction, PII minimisation, and confidence calculation."""

from __future__ import annotations

import copy
import re
from typing import Any

_LABELS = {
    "skills": "Skills match",
    "education": "Education",
    "experience": "Experience relevance",
    "projects": "Project quality",
    "links": "Links and evidence",
}


def _compact(value: Any, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def redact_for_semantic_scoring(
    resume_text: str, parsed_resume_json: dict
) -> tuple[str, dict]:
    """Remove direct identity/contact fields before external semantic scoring.

    The original parsed data remains available to the recruiter; only the Jev
    payload is minimised. This cannot infer every protected attribute, so the
    no-auto-rejection policy remains mandatory.
    """
    redacted = copy.deepcopy(parsed_resume_json or {})
    header = redacted.get("header")
    values: list[str] = []
    if isinstance(header, dict):
        for key in ("fullName", "email", "phone", "location", "address", "dateOfBirth"):
            value = header.pop(key, None)
            if isinstance(value, str) and len(value.strip()) >= 3:
                values.append(value.strip())
    text = resume_text
    for value in sorted(values, key=len, reverse=True):
        text = re.sub(re.escape(value), "[REDACTED]", text, flags=re.I)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]", text)
    text = re.sub(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)", "[REDACTED_PHONE]", text)
    return text, redacted


def build_category_evidence(
    *,
    resume_text: str = "",
    parsed_resume_json: dict,
    required_skills: list[str],
    preferred_skills: list[str],
    link_report: dict,
    jev_report: dict,
) -> dict[str, list[dict]]:
    parsed = parsed_resume_json or {}
    resume_skills = parsed.get("skills") or []
    skill_strings = [
        str(item.get("name") if isinstance(item, dict) else item)
        for item in resume_skills
    ]
    skill_lookup = {item.casefold(): item for item in skill_strings if item.strip()}
    requested = required_skills + preferred_skills
    matched = [
        skill_lookup[s.casefold()] for s in requested if s.casefold() in skill_lookup
    ]
    missing = [s for s in required_skills if s.casefold() not in skill_lookup]

    evidence: dict[str, list[dict]] = {key: [] for key in _LABELS}

    def with_location(item: dict) -> dict:
        text = str(item.get("text") or "")
        if resume_text and text:
            lines = resume_text.splitlines()
            needle = text[:80].casefold()
            for index, line in enumerate(lines):
                if needle and needle in line.casefold():
                    item["lineStart"] = index + 1
                    item["lineEnd"] = index + 1
                    break
        return item
    if matched:
        evidence["skills"].append(with_location(
            {"source": "resume.skills", "text": f"Matched: {', '.join(matched[:12])}"}
        ))
    if missing:
        evidence["skills"].append(with_location({
                "source": "role.requiredSkills",
                "text": f"Not evidenced: {', '.join(missing[:12])}",
            }))
    if skill_strings and not requested:
            evidence["skills"].append(with_location({
                "source": "resume.skills",
                "text": f"Candidate lists: {', '.join(skill_strings[:12])}",
            }))

    for key, source in (
        ("education", "resume.education"),
        ("experience", "resume.experience"),
        ("projects", "resume.projects"),
    ):
        entries = parsed.get(key) or []
        for entry in entries[:3] if isinstance(entries, list) else []:
            if isinstance(entry, dict):
                preferred_fields = {
                    "education": ("degree", "institution", "field"),
                    "experience": (
                        "role",
                        "company",
                        "responsibilities",
                        "description",
                    ),
                    "projects": ("name", "description", "technologies"),
                }[key]
                fragments = [
                    _compact(entry.get(field), 100)
                    for field in preferred_fields
                    if entry.get(field)
                ]
                text = " — ".join(fragments)
            else:
                text = _compact(entry)
            if text:
                evidence[key].append(with_location({"source": source, "text": _compact(text)}))

    missing_links = (link_report or {}).get("missing_required") or []
    if missing_links:
            evidence["links"].append(with_location({
                "source": "link.validation",
                "text": f"Missing required evidence: {', '.join(map(str, missing_links))}",
            }))
    else:
        evidence["links"].append(with_location({
                "source": "link.validation",
                "text": "No role-required professional link is missing.",
            }))

    question_for = {
        "skills": "skills_score",
        "education": "education_quality",
        "experience": "experience_relevance",
        "projects": "project_quality",
    }
    for category, question in question_for.items():
        answer = (jev_report or {}).get(question) or {}
        if answer.get("value") is not None:
            evidence[category].append(with_location({
                    "source": f"jev.{question}",
                    "text": f"Jev assessment: {answer['value']} (confidence {answer.get('confidence')})",
                }))
    return evidence


def calculate_confidence(
    *,
    role_context: str,
    category_evidence: dict[str, list[dict]],
    jev_average_confidence: float | None,
    parsed_resume_json: dict,
    warnings: list[str],
    final_score: float,
    jd_quality_score: float | None = None,
) -> dict:
    role_score = {
        "job_description": 0.95,
        "evaluation_intent_and_skills": 0.82,
        "evaluation_intent": 0.68,
        "role_and_skills": 0.75,
        "role_only": 0.45,
    }.get(role_context, 0.45)
    if role_context == "job_description" and jd_quality_score is not None:
        role_score = max(0.45, min(0.95, 0.45 + 0.50 * (jd_quality_score / 100.0)))
    evidence_coverage = sum(bool(category_evidence.get(key)) for key in _LABELS) / len(
        _LABELS
    )
    parsed_sections = (
        sum(
            bool((parsed_resume_json or {}).get(key))
            for key in ("skills", "education", "experience", "projects")
        )
        / 4
    )
    semantic = jev_average_confidence if jev_average_confidence is not None else 0.35
    penalty = min(0.2, 0.025 * len(set(warnings)))
    overall = max(
        0.05,
        min(
            0.99,
            0.30 * role_score
            + 0.30 * semantic
            + 0.25 * evidence_coverage
            + 0.15 * parsed_sections
            - penalty,
        ),
    )
    margin = 5.0 + (1.0 - overall) * 20.0
    category = {}
    for key in _LABELS:
        has_evidence = bool(category_evidence.get(key))
        value = max(
            0.05,
            min(0.99, 0.45 * role_score + 0.35 * semantic + 0.20 * float(has_evidence)),
        )
        category[key] = round(value, 4)
    return {
        "overall": round(overall, 4),
        "level": "high" if overall >= 0.8 else "medium" if overall >= 0.6 else "low",
        "scoreInterval": {
            "lower": round(max(0.0, final_score - margin), 2),
            "upper": round(min(100.0, final_score + margin), 2),
        },
        "categoryConfidence": category,
        "factors": {
            "roleContext": round(role_score, 4),
            "jev": round(semantic, 4),
            "evidenceCoverage": round(evidence_coverage, 4),
            "parserCompleteness": round(parsed_sections, 4),
            "warningPenalty": round(penalty, 4),
        },
        "policy": "Confidence controls review priority; it never changes the candidate score or automatically rejects a candidate.",
    }
