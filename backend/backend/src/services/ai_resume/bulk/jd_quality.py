"""Deterministic job-description quality and safety checks.

This module deliberately does not use an LLM: recruiters should get the same
quality score for the same rubric, and potentially discriminatory wording must
be surfaced even when an AI provider is unavailable.
"""

from __future__ import annotations

import re

_SECTION_PATTERNS = {
    "responsibilities": r"\b(responsibilit(?:y|ies)|you will|what you(?:'|’)ll do|duties)\b",
    "experience": r"\b(?:minimum|at least|\d+\+?)\s*(?:years?|yrs?)\b|\bexperience\b",
    "education": r"\b(degree|bachelor|master|ph\.?d|education|equivalent experience)\b",
    "outcomes": r"\b(outcome|impact|deliver|improve|reduce|increase|success|metric|kpi)\b",
    "domain": r"\b(domain|industry|customer|product|platform|business)\b",
}

_RISKY_WORDING = {
    "age_or_seniority_proxy": r"\b(young|youthful|digital native|recent graduate)\b",
    "sex_or_gender": r"\b(male|female|man only|woman only)\b",
    "nationality_or_language_proxy": r"\b(native english speaker|native speaker|nationality)\b",
    "family_status": r"\b(single|married|marital status|no children)\b",
    "religion_or_caste": r"\b(religion|religious|caste)\b",
    "health_or_disability": r"\b(able[- ]bodied|no disabilities|perfect health)\b",
}


def analyze_job_description(
    description: str | None,
    *,
    required_skills: list[str] | None = None,
    preferred_skills: list[str] | None = None,
) -> dict:
    """Return an auditable 0-100 quality score and improvement warnings.

    A JD remains optional. When absent, supplied skill rubrics receive partial
    credit, but the result is marked limited so downstream ranking confidence
    can be reduced and human review can be prioritised.
    """
    text = (description or "").strip()
    lowered = text.casefold()
    words = re.findall(r"\b[\w+#.-]+\b", text)
    required = [skill.strip() for skill in (required_skills or []) if skill.strip()]
    preferred = [skill.strip() for skill in (preferred_skills or []) if skill.strip()]
    signals = {
        name: bool(re.search(pattern, lowered, re.I))
        for name, pattern in _SECTION_PATTERNS.items()
    }
    risky = [
        name
        for name, pattern in _RISKY_WORDING.items()
        if re.search(pattern, lowered, re.I)
    ]

    checks = {
        "descriptionProvided": bool(text),
        "adequateLength": 80 <= len(words) <= 1500,
        "responsibilities": signals["responsibilities"],
        "mustHaveSkills": bool(required)
        or bool(re.search(r"\b(must[- ]have|required|requirements)\b", lowered)),
        "preferredSkills": bool(preferred)
        or bool(re.search(r"\b(preferred|nice to have|desirable)\b", lowered)),
        "experience": signals["experience"],
        "educationOrEquivalent": signals["education"],
        "measurableOutcomes": signals["outcomes"],
        "domainContext": signals["domain"],
        "inclusiveWording": not risky,
    }
    weights = {
        "adequateLength": 10,
        "responsibilities": 15,
        "mustHaveSkills": 20,
        "preferredSkills": 5,
        "experience": 10,
        "educationOrEquivalent": 5,
        "measurableOutcomes": 10,
        "domainContext": 10,
        "inclusiveWording": 15,
    }
    score = sum(weight for key, weight in weights.items() if checks[key])
    if not text:
        score = min(score, 35)

    warnings: list[str] = []
    if not text:
        warnings.append("job_description_not_provided")
    elif len(words) < 80:
        warnings.append("job_description_too_brief")
    elif len(words) > 1500:
        warnings.append("job_description_too_long")
    for key in (
        "responsibilities",
        "mustHaveSkills",
        "experience",
        "measurableOutcomes",
        "domainContext",
    ):
        if not checks[key]:
            warnings.append(f"missing_{key}")
    if risky:
        warnings.append("potentially_discriminatory_criteria_detected")

    level = "strong" if score >= 80 else "adequate" if score >= 60 else "limited"
    return {
        "score": score,
        "level": level,
        "wordCount": len(words),
        "checks": checks,
        "warnings": warnings,
        "riskyCriteria": risky,
        "extractedSignals": {
            "requiredSkills": required,
            "preferredSkills": preferred,
            "hasExperienceRequirement": signals["experience"],
            "hasEducationRequirement": signals["education"],
            "hasResponsibilities": signals["responsibilities"],
            "hasOutcomeLanguage": signals["outcomes"],
            "hasDomainContext": signals["domain"],
        },
        "policy": "Advisory only; never use protected characteristics or this quality score to reject a candidate.",
    }
