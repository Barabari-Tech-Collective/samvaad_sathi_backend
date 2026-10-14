"""Deterministic, role-track link requirement checks for Bulk ATS.

Built on top of LinkScorer.score_links()'s "categoryBreakdown" (linkedin/
github/portfolio/deployments/packages/certifications, each {"present",
"working", ...}) rather than re-validating URLs - SmartLinkValidator +
LinkScorer already do the network check and platform classification; this
module only decides which categories are *required* for a given role track,
which LinkScorer itself is track-agnostic about. "github" here already
covers both a GitHub profile and repository links (see link_scorer.py); it
has no separate "repositories" entry.
"""

from __future__ import annotations

from typing import Any, Literal

RoleTrack = Literal["engineering", "design", "data", "non_tech"]


def infer_role_track(role_name: str) -> RoleTrack:
    name = (role_name or "").lower()
    if any(k in name for k in ("data", "analyst", "scientist", "ml", "machine learning")):
        return "data"
    if any(k in name for k in ("design", "ui", "ux")):
        return "design"
    if any(k in name for k in ("developer", "engineer", "backend", "frontend", "full stack", "fullstack", "software", "devops", "sde")):
        return "engineering"
    return "non_tech"


def check_link_requirements(category_breakdown: dict[str, dict], role_track: RoleTrack) -> dict[str, Any]:
    """Returns {"passed": [...], "missing_required": [...], "warnings": [...]}
    for one candidate's LinkScorer categoryBreakdown."""

    def is_working(category: str) -> bool:
        return bool(category_breakdown.get(category, {}).get("working"))

    has_linkedin = is_working("linkedin")
    has_github_or_portfolio = is_working("github") or is_working("portfolio")
    has_project_link = is_working("deployments") or category_breakdown.get("github", {}).get("repositoryCount", 0) > 0

    passed: list[str] = []
    missing_required: list[str] = []
    warnings: list[str] = []

    if has_linkedin:
        passed.append("linkedin_present")
    else:
        missing_required.append("linkedin_missing")

    if role_track == "engineering":
        if has_github_or_portfolio:
            passed.append("github_or_portfolio_present")
        else:
            missing_required.append("github_or_portfolio_missing")
        if not has_project_link:
            warnings.append("no_live_or_repo_project_link")
    elif role_track == "data":
        # GitHub/portfolio optional for data roles, but a project/dataset
        # link is valuable - tracked as a warning, never a hard requirement.
        if not (has_github_or_portfolio or has_project_link):
            warnings.append("no_data_project_link")
    elif role_track == "design":
        if is_working("portfolio"):
            passed.append("portfolio_present")
        else:
            missing_required.append("portfolio_missing")
    else:  # non_tech
        if is_working("portfolio"):
            passed.append("portfolio_present")
        else:
            warnings.append("portfolio_missing_preferred")

    return {"passed": passed, "missing_required": missing_required, "warnings": warnings, "role_track": role_track}
