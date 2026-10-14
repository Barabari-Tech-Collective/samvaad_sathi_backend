"""Category weight tables for Bulk ATS final scoring.

A single universal score is misleading across roles with very different
shapes (a fresher UI designer vs. an experienced data analyst), so weights
vary by experience mode, not by role - role-specific judgment (is this skill
relevant, is this project substantial) is Jev's job per QUESTIONS in
jev_client.py, not a per-role weight table here.
"""

from __future__ import annotations

CATEGORY_KEYS = ("skills", "education", "experience", "projects", "links")

# Percent points, must sum to 100 per mode. Fresher has no experience
# category - its weight is redistributed into skills/projects (the two
# categories a fresher can actually demonstrate).
DEFAULT_WEIGHTS: dict[str, dict[str, int]] = {
    "experienced": {"skills": 30, "education": 15, "experience": 20, "projects": 25, "links": 10},
    "fresher": {"skills": 35, "education": 20, "experience": 0, "projects": 35, "links": 10},
}

SCORE_BANDS: tuple[tuple[float, str], ...] = (
    (85.0, "Excellent"),
    (70.0, "Good"),
    (50.0, "Average"),
    (0.0, "Weak"),
)


def resolve_weights(experience_mode: str, overrides: dict | None) -> dict[str, int]:
    """Merge a batch's weight_overrides onto the mode default, then
    renormalize to 100 so a partial override can't silently change the scale
    of the final score."""
    base = dict(DEFAULT_WEIGHTS.get(experience_mode, DEFAULT_WEIGHTS["experienced"]))
    if overrides:
        for key in CATEGORY_KEYS:
            if key in overrides and isinstance(overrides[key], (int, float)):
                base[key] = overrides[key]

    total = sum(base.values()) or 1
    return {key: (value / total) * 100 for key, value in base.items()}


def score_band_for(final_score: float) -> str:
    for threshold, band in SCORE_BANDS:
        if final_score >= threshold:
            return band
    return "Weak"
