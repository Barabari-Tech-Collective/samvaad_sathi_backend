"""Evidence extraction, PII minimisation, and confidence calculation."""

from __future__ import annotations

import copy
import re
from typing import Any
from src.services.ai_resume.scoring.skill_normalizer import SkillNormalizer

_normalizer = SkillNormalizer()

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


def _check_skill_match(
    req: str,
    parsed_skills: list[str],
    candidate_canonicals: set[str],
    candidate_clean_tokens: set[str],
    resume_text: str,
    normalizer: SkillNormalizer,
) -> bool:
    """
    Universal multi-domain skill verification:
    1. Canonical alias matching (SkillNormalizer)
    2. Parsed skills direct & qualifier stripping (e.g., 'Microsoft Excel (advanced)')
    3. Word-level subphrase matching in compound skills (e.g., 'Programming & Data Analysis')
    4. Raw résumé text scanning with symbol-safe word boundaries across all domains
    """
    if not req or not isinstance(req, str):
        return False

    req_clean = req.strip().casefold()
    if not req_clean:
        return False

    # 1. Canonical normalization check
    req_canon = normalizer.normalize(req_clean)
    if req_canon and req_canon in candidate_canonicals:
        return True

    # 2. Direct casefolded token or candidate match
    if req_clean in candidate_clean_tokens:
        return True

    # Stripped parenthetical check: e.g. "exploratory data analysis (eda)" -> "exploratory data analysis"
    req_no_paren = re.sub(r"\(.*?\)", "", req_clean).strip()
    if req_no_paren and req_no_paren in candidate_clean_tokens:
        return True

    # 3. Check parsed skill entries for word-level subphrases or qualifiers
    for cand in parsed_skills:
        cand_clean = cand.strip().casefold()
        if not cand_clean:
            continue

        cand_no_paren = re.sub(r"\(.*?\)", "", cand_clean).strip()
        if req_clean == cand_clean or req_clean == cand_no_paren:
            return True

        # Check if req is a whole subphrase in cand (e.g. "data analysis" in "programming & data analysis")
        escaped_req = re.escape(req_no_paren if req_no_paren else req_clean)
        if re.search(r'(?i)(?:^|(?<=[^a-zA-Z0-9_]))' + escaped_req + r'(?:$|(?=[^a-zA-Z0-9_]))', cand_clean):
            return True

        # Check qualifiers (e.g. req="Advanced Excel" and cand has "advanced" + "excel")
        if "advanced" in req_clean and "excel" in req_clean:
            if "advanced" in cand_clean and "excel" in cand_clean:
                return True
        if "basic" in req_clean and "excel" in req_clean:
            if "excel" in cand_clean:
                return True

    # 4. Raw resume text search (Universal fallback across all roles and domains)
    if resume_text:
        text_lower = resume_text.lower()

        # If canonical is known, check canonical aliases in raw text
        if req_canon and req_canon in normalizer.alias_matrix.values():
            matching_aliases = [
                alias for alias, canon in normalizer.alias_matrix.items() if canon == req_canon
            ]
            for alias in matching_aliases:
                escaped = re.escape(alias)
                pat = r"(?:^|(?<=[^a-zA-Z0-9_]))" + escaped + r"(?:$|(?=[^a-zA-Z0-9_]))"
                if re.search(pat, text_lower):
                    return True

        # Special domain combinations:
        # e.g., "Advanced Excel": match "advanced excel", "excel (advanced)", "microsoft excel (advanced)"
        if "advanced" in req_clean and "excel" in req_clean:
            if re.search(r'(?i)\b(?:advanced\s+(?:ms\s+|microsoft\s+)?excel|(?:ms\s+|microsoft\s+)?excel\s*\(?advanced\)?)\b', resume_text):
                return True

        # e.g., "Microsoft Excel": match "microsoft excel" or "ms excel" or "excel"
        if req_clean in {"microsoft excel", "excel", "ms excel"}:
            if re.search(r'(?i)\b(?:microsoft\s+excel|ms\s+excel|excel)\b', resume_text):
                return True

        # Flexible phrase search in raw resume text for any skill (supporting spaces, hyphens, slashes)
        search_target = req_no_paren if req_no_paren else req_clean
        if len(search_target) >= 2:
            escaped_words = [re.escape(w) for w in re.split(r'[\s\-_/]+', search_target) if w]
            if escaped_words:
                flex_pattern = r'[\s\-_/]+'.join(escaped_words)
                pat = r"(?i)(?:^|(?<=[^a-zA-Z0-9_]))" + flex_pattern + r"(?:$|(?=[^a-zA-Z0-9_]))"
                if re.search(pat, text_lower):
                    return True

    return False


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
    deterministic_report: dict | None = None,
) -> dict[str, list[dict]]:
    parsed = parsed_resume_json or {}
    resume_skills = parsed.get("skills") or []
    skill_strings = [
        str(item.get("name") if isinstance(item, dict) else item)
        for item in resume_skills
    ]
    requested = required_skills + preferred_skills

    candidate_canonicals: set[str] = set()
    candidate_clean_tokens: set[str] = set()

    for item in skill_strings:
        c_str = item.strip()
        if not c_str:
            continue
        candidate_clean_tokens.add(c_str.casefold())
        c_no_paren = re.sub(r"\(.*?\)", "", c_str).strip().casefold()
        if c_no_paren:
            candidate_clean_tokens.add(c_no_paren)

        for t in _normalizer.normalize_tokens(c_str):
            candidate_canonicals.add(t)
        direct_c = _normalizer.normalize(c_str)
        if direct_c:
            candidate_canonicals.add(direct_c)

    matched: list[str] = []
    missing: list[str] = []
    for req in requested:
        if _check_skill_match(
            req=req,
            parsed_skills=skill_strings,
            candidate_canonicals=candidate_canonicals,
            candidate_clean_tokens=candidate_clean_tokens,
            resume_text=resume_text,
            normalizer=_normalizer,
        ):
            if req not in matched:
                matched.append(req)
        else:
            if req in required_skills and req not in missing:
                missing.append(req)

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
            {"source": "resume.skills", "text": f"Matched: {', '.join(matched[:30])}"}
        ))
    if missing:
        evidence["skills"].append(with_location({
                "source": "role.requiredSkills",
                "text": f"Not evidenced: {', '.join(missing[:30])}",
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
                if key == "education":
                    edu_fragments = []
                    for field in ("degree", "institution", "field"):
                        val = entry.get(field)
                        if val:
                            edu_fragments.append(_compact(val, 100))

                    grade_val = (
                        entry.get("grade")
                        or entry.get("cgpa")
                        or entry.get("gpa")
                        or entry.get("cpi")
                        or entry.get("percentage")
                        or entry.get("marks")
                    )
                    if grade_val:
                        g_str = str(grade_val).strip()
                        if not any(token in g_str.lower() for token in ("cgpa", "gpa", "grade", "%", "marks", "cpi")):
                            g_str = f"CGPA: {g_str}"
                        edu_fragments.append(_compact(g_str, 60))
                    text = " — ".join(edu_fragments)
                else:
                    preferred_fields = {
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

    # Surface detected CGPA / academic performance from deterministic analysis if not already visible
    if deterministic_report:
        edu_mod = (
            deterministic_report.get("deterministicMetrics", {}).get("educationModule", {})
            or deterministic_report.get("educationAnalysis", {})
        )
        grade_analysis = edu_mod.get("educationAnalysis", {}).get("grade", {})
        detected_grade = grade_analysis.get("detectedValue")
        if grade_analysis.get("present") and detected_grade and detected_grade != "Missing":
            already_shown = any(
                str(detected_grade).lower() in str(item.get("text", "")).lower()
                for item in evidence["education"]
            )
            if not already_shown:
                evidence["education"].append(with_location({
                    "source": "resume.education",
                    "text": f"Academic score: {detected_grade}",
                }))

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

    # Return clean, candidate-grounded resume evidence only.
    # Provider-internal notes (e.g. Jev raw values and confidence metrics) are omitted
    # from resume details so recruiters and students see only authentic resume evidence.
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
