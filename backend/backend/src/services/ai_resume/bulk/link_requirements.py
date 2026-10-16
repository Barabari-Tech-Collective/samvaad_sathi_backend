"""Role-aware evidence policy for Bulk ATS.

Links are useful recruiter evidence, but their absence is not a universal
measure of candidate quality. This module therefore scores only link types
that the recruiter explicitly requested. All discovered links remain visible.
"""

from __future__ import annotations

from typing import Any, Iterable, Literal
from urllib.parse import urlparse

from src.services.ai_resume.bulk.domain_profile import DomainProfile

RoleTrack = Literal["engineering", "design", "data", "non_tech"]


def infer_role_track(role_name: str) -> RoleTrack:
    """Compatibility helper for the legacy deterministic link scorer."""
    name = (role_name or "").lower()
    if any(k in name for k in ("data", "analyst", "scientist", "machine learning")):
        return "data"
    if any(k in name for k in ("design", "ui", "ux")):
        return "design"
    if any(
        k in name
        for k in (
            "developer",
            "engineer",
            "backend",
            "frontend",
            "full stack",
            "fullstack",
            "software",
            "devops",
            "sde",
        )
    ):
        return "engineering"
    return "non_tech"


def _safe_link_records(verified_links: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    links = verified_links.get("links", {}) if isinstance(verified_links, dict) else {}
    if not isinstance(links, dict):
        return records
    for raw_url, raw_details in links.items():
        details = raw_details if isinstance(raw_details, dict) else {}
        normalized_url = str(raw_url).strip()
        if normalized_url and not normalized_url.lower().startswith(
            ("http://", "https://")
        ):
            normalized_url = f"https://{normalized_url}"
        parsed = urlparse(normalized_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        anchor = str(details.get("anchorText") or "").strip()
        platform = str(details.get("platform") or "").strip()
        records.append(
            {
                "url": normalized_url,
                "label": anchor or platform.title() or parsed.netloc,
                "platform": platform or None,
                "category": str(details.get("category") or "other").strip(),
                "working": bool(details.get("valid")),
                "statusCode": details.get("status_code"),
                "page": details.get("page"),
                "section": details.get("section"),
            }
        )
    return records


def _matches_requirement(link: dict[str, Any], requirement: str) -> bool:
    category = str(link.get("category") or "").lower()
    platform = str(link.get("platform") or "").lower()
    url = str(link.get("url") or "").lower()
    if requirement == "repository":
        return category == "repository" or "github.com/" in url or "gitlab.com/" in url
    if requirement == "portfolio":
        return category in {"portfolio", "design"} or platform in {
            "behance",
            "dribbble",
            "figma",
        }
    if requirement == "linkedin":
        return platform == "linkedin" or "linkedin.com/" in url
    if requirement == "project_evidence":
        return category in {
            "repository",
            "deployment",
            "portfolio",
            "design",
            "blog",
            "project",
            "dashboard",
        }
    return False


def check_link_requirements(
    category_breakdown: dict[str, dict] | None = None,
    role_track: RoleTrack | None = None,
    *,
    verified_links: dict[str, Any] | None = None,
    required_categories: Iterable[str] = (),
    domain_profile: DomainProfile | None = None,
) -> dict[str, Any]:
    """Build an explainable evidence report without implicit link penalties."""
    del category_breakdown
    links = _safe_link_records(verified_links or {})
    required = tuple(dict.fromkeys(str(item) for item in required_categories))
    passed: list[str] = []
    missing_required: list[str] = []
    for requirement in required:
        matches = [link for link in links if _matches_requirement(link, requirement)]
        if any(link["working"] for link in matches):
            passed.append(f"{requirement}_working")
        elif matches:
            missing_required.append(f"required_{requirement}_not_working")
        else:
            missing_required.append(f"required_{requirement}_missing")

    score = round((len(passed) / len(required)) * 100.0, 2) if required else None
    return {
        "passed": passed,
        "missing_required": missing_required,
        "warnings": [],
        "role_track": role_track or "non_tech",
        "domain": domain_profile.id if domain_profile else "general_business",
        "domainLabel": domain_profile.label if domain_profile else "General business",
        "scoreApplicable": bool(required),
        "linkScore": score,
        "requiredEvidence": list(required),
        "recommendedEvidence": (
            list(domain_profile.recommended_evidence) if domain_profile else []
        ),
        "links": links,
    }
