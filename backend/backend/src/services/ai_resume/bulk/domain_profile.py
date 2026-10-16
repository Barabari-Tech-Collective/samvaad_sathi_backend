"""Domain and evidence-policy inference for fair, role-aware Bulk ATS scoring."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal

DomainId = Literal[
    "software_engineering",
    "data_analytics",
    "design_creative",
    "product_management",
    "sales_marketing",
    "customer_success_support",
    "finance_accounting",
    "operations_supply_chain",
    "human_resources",
    "education_social_impact",
    "healthcare_life_sciences",
    "legal_compliance",
    "general_business",
]


@dataclass(frozen=True)
class DomainProfile:
    id: DomainId
    label: str
    recommended_evidence: tuple[str, ...]
    project_guidance: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "recommendedEvidence": list(self.recommended_evidence),
            "projectGuidance": self.project_guidance,
        }


_PROFILES: dict[DomainId, DomainProfile] = {
    "software_engineering": DomainProfile(
        "software_engineering",
        "Software engineering",
        ("repository", "deployment", "technical portfolio"),
        "Judge ownership, role relevance, outcomes, technical decisions, and supporting evidence.",
    ),
    "data_analytics": DomainProfile(
        "data_analytics",
        "Data and analytics",
        ("dashboard", "case study", "dataset or notebook", "report"),
        "Judge the question framed, data work, analytical method, insight, decision impact, and evidence.",
    ),
    "design_creative": DomainProfile(
        "design_creative",
        "Design and creative",
        ("portfolio", "case study", "prototype"),
        "Judge problem framing, contribution, process, craft, validation, and outcome.",
    ),
    "product_management": DomainProfile(
        "product_management",
        "Product management",
        ("case study", "product brief", "research or launch evidence"),
        "Judge problem discovery, prioritisation, ownership, cross-functional work, and measurable outcome.",
    ),
    "sales_marketing": DomainProfile(
        "sales_marketing",
        "Sales and marketing",
        ("campaign", "content portfolio", "case study", "performance report"),
        "Judge audience insight, ownership, execution, channel choices, and measurable commercial outcome.",
    ),
    "customer_success_support": DomainProfile(
        "customer_success_support",
        "Customer success and support",
        ("case study", "process improvement", "customer outcome"),
        "Judge customer problem solving, communication, ownership, process, and outcome.",
    ),
    "finance_accounting": DomainProfile(
        "finance_accounting",
        "Finance and accounting",
        ("analysis", "model", "report", "relevant credential"),
        "Judge analytical rigour, ownership, controls, accuracy, and business outcome.",
    ),
    "operations_supply_chain": DomainProfile(
        "operations_supply_chain",
        "Operations and supply chain",
        ("process map", "analysis", "improvement case study", "report"),
        "Judge process understanding, ownership, coordination, efficiency, and measurable outcome.",
    ),
    "human_resources": DomainProfile(
        "human_resources",
        "People and human resources",
        ("program case study", "process improvement", "people outcome"),
        "Judge stakeholder work, ownership, process quality, communication, and outcome.",
    ),
    "education_social_impact": DomainProfile(
        "education_social_impact",
        "Education and social impact",
        ("program evidence", "curriculum", "research", "impact report"),
        "Judge learner or community need, contribution, delivery, inclusion, and measured impact.",
    ),
    "healthcare_life_sciences": DomainProfile(
        "healthcare_life_sciences",
        "Healthcare and life sciences",
        ("research", "clinical or operational project", "relevant credential"),
        "Judge role relevance, rigour, contribution, safety-aware process, and outcome.",
    ),
    "legal_compliance": DomainProfile(
        "legal_compliance",
        "Legal and compliance",
        ("research", "policy work", "case analysis", "relevant credential"),
        "Judge research quality, ownership, reasoning, risk awareness, and outcome.",
    ),
    "general_business": DomainProfile(
        "general_business",
        "General business",
        ("case study", "work sample", "report", "relevant credential"),
        "Judge clarity, ownership, role relevance, execution, and measurable outcome.",
    ),
}

_KEYWORDS: dict[DomainId, tuple[str, ...]] = {
    "software_engineering": (
        "software",
        "developer",
        "engineer",
        "frontend",
        "backend",
        "full stack",
        "devops",
        "sre",
        "mobile developer",
        "qa engineer",
        "cybersecurity",
    ),
    "data_analytics": (
        "data analyst",
        "business analyst",
        "analytics",
        "data science",
        "data scientist",
        "business intelligence",
        "power bi",
        "tableau",
        "sql",
        "machine learning",
    ),
    "design_creative": (
        "designer",
        "ui",
        "ux",
        "user experience",
        "graphic design",
        "visual design",
        "content design",
        "creative",
        "figma",
    ),
    "product_management": (
        "product manager",
        "product management",
        "product owner",
        "product strategy",
        "product operations",
        "product analyst",
    ),
    "sales_marketing": (
        "sales",
        "marketing",
        "growth",
        "brand",
        "seo",
        "campaign",
        "content marketing",
        "business development",
        "account executive",
    ),
    "customer_success_support": (
        "customer success",
        "customer support",
        "customer service",
        "support specialist",
        "implementation specialist",
        "account manager",
    ),
    "finance_accounting": (
        "finance",
        "accounting",
        "accountant",
        "audit",
        "tax",
        "investment",
        "banking",
        "financial analyst",
        "risk analyst",
    ),
    "operations_supply_chain": (
        "operations",
        "supply chain",
        "logistics",
        "procurement",
        "quality management",
        "program operations",
        "project coordinator",
    ),
    "human_resources": (
        "human resources",
        "hr ",
        "recruiter",
        "talent acquisition",
        "people operations",
        "learning and development",
    ),
    "education_social_impact": (
        "teacher",
        "education",
        "curriculum",
        "facilitator",
        "social work",
        "ngo",
        "community",
        "program manager",
        "trainer",
    ),
    "healthcare_life_sciences": (
        "healthcare",
        "clinical",
        "medical",
        "nurse",
        "pharma",
        "biotechnology",
        "life sciences",
        "public health",
    ),
    "legal_compliance": (
        "legal",
        "lawyer",
        "counsel",
        "compliance",
        "policy",
        "governance",
        "regulatory",
    ),
    "general_business": ("business", "management", "consulting", "administration"),
}


def _matches(text: str, phrase: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(phrase.strip())}(?!\w)", text))


def infer_domain_profile(
    *,
    role_name: str,
    role_description: str = "",
    evaluation_intent: str = "",
    required_skills: Iterable[str] = (),
    preferred_skills: Iterable[str] = (),
) -> DomainProfile:
    """Infer a broad evaluation domain from recruiter-controlled context.

    The role name is weighted most heavily so incidental JD words cannot turn a
    product or operations role into engineering merely because it collaborates
    with developers.
    """

    role = (role_name or "").casefold()
    skills = " ".join([*required_skills, *preferred_skills]).casefold()
    context = f"{role_description} {evaluation_intent}".casefold()
    scores: dict[DomainId, int] = {key: 0 for key in _PROFILES}
    for domain, phrases in _KEYWORDS.items():
        for phrase in phrases:
            scores[domain] += 6 if _matches(role, phrase) else 0
            scores[domain] += 3 if _matches(skills, phrase) else 0
            scores[domain] += 1 if _matches(context, phrase) else 0

    selected = max(scores, key=lambda domain: scores[domain])
    if scores[selected] == 0:
        selected = "general_business"
    return _PROFILES[selected]


def infer_candidate_field(parsed_resume_json: dict) -> DomainProfile:
    parsed = parsed_resume_json or {}
    experience_titles = " ".join(
        str(item.get("title") or item.get("role") or "")
        for item in (parsed.get("experience") or [])
        if isinstance(item, dict)
    )
    skills = [
        str(item.get("name") if isinstance(item, dict) else item)
        for item in (parsed.get("skills") or [])
    ]
    return infer_domain_profile(
        role_name=experience_titles,
        role_description=str(parsed.get("summary") or ""),
        required_skills=skills,
    )


def explicit_link_requirements(
    *,
    role_description: str,
    evaluation_intent: str,
    required_skills: Iterable[str],
) -> tuple[str, ...]:
    """Return only link types the recruiter rubric explicitly asks to inspect."""

    text = " ".join(
        [role_description, evaluation_intent, " ".join(required_skills)]
    ).casefold()
    patterns = {
        "repository": (
            r"github\s+(?:link|profile|repository|required)",
            r"(?:require|required|must\s+(?:provide|include))[^.]{0,30}github",
            r"(?:repository|source\s+code)\s+link",
        ),
        "portfolio": (
            r"portfolio\s+(?:link|required|url)",
            r"(?:behance|dribbble|figma)\s+(?:link|portfolio)",
        ),
        "linkedin": (r"linkedin\s+(?:link|profile|required|url)",),
        "project_evidence": (
            r"(?:project|case\s+study|dashboard|work\s+sample)\s+(?:link|url|required)",
            r"(?:live\s+demo|deployment)\s+(?:link|required|url)?",
        ),
    }
    return tuple(
        category
        for category, expressions in patterns.items()
        if any(re.search(expression, text) for expression in expressions)
    )
