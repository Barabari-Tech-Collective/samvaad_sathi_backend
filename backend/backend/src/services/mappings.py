from __future__ import annotations

# Change: Extracted TRACK_TO_CATEGORY mapping from analytics service into this dedicated mappings module.
# Why it was made: Decouples static role/domain category mappings from core analytics logic, keeping
# service classes clean and making track additions/updates maintainable in one place.
TRACK_TO_CATEGORY: dict[str, list[str]] = {
    "it": [
        "full stack developer",
        "javascript developer",
        "react developer",
        "node js developer",
        "express js developer",
        "ui developer",
        "developer",
        "frontend developer",
        "backend developer",
        "software engineer",
        "web developer",
        "devops engineer",
        "cloud engineer",
        "qa engineer",
        "software developer",
    ],
    "design": [
        "ui/ux",
        "ui/ux designer",
        "product designer",
        "product desigmer",
        "non-tech: ui/ux designer",
        "non-tech: product desigmer",
        "non-tech: ui/ux",
        "graphic designer",
    ],
    "data": [
        "data analyst",
        "data analystics",
        "data analysis",
        "data scientist",
        "data engineer",
        "machine learning engineer",
        "business intelligence",
    ],
    "sales": [
        "sales",
        "sales executive",
        "sales representative",
        "business development",
        "account executive",
        "inside sales",
    ],
    "marketing": [
        "marketing",
        "digital marketing",
        "marketing specialist",
        "seo specialist",
        "content marketing",
        "growth marketing",
        "social media marketing",
    ],
    "hr": [
        "hr",
        "human resources",
        "hr executive",
        "talent acquisition",
        "technical recruiter",
        "hr manager",
        "hr generalist",
    ],
    "operations": [
        "operations",
        "operations manager",
        "operations associate",
        "supply chain",
        "logistics",
        "business operations",
    ],
}

