import re
from typing import Dict, Any, List, Set, Optional
from src.services.ai_resume.scoring.skill_normalizer import SkillNormalizer


class SkillsScorer:
    """
    Deterministic Skills Scorer for the ATS Engine.
    - Normalizes resume/JD skills into canonical IDs.
    - Uses exact skill matching for strong skills.
    - Reports related technologies separately.
    - Avoids treating different technologies as exact equivalents.
    - Scans raw text using symbol-safe boundary regex.
    """

    def __init__(self):
        self.normalizer = SkillNormalizer()

    def score_skills(
        self,
        resume_skills: Optional[List[str]] = None,
        jd_skills: Optional[List[str]] = None,
        raw_resume_text: str = "",
        raw_jd_text: str = ""
    ) -> Dict[str, Any]:

        final_resume_set = self._extract_and_normalize(resume_skills, raw_resume_text)
        final_jd_set = self._extract_and_normalize(jd_skills, raw_jd_text)

        if not final_jd_set:
            return {
                "totalScore": 0.0,
                "maxScore": 25.0,
                "skillsAnalysis": {
                    "strongSkills": [],
                    "missingSkills": [],
                    "relatedSkills": [],
                    "additionalSkills": [],
                    "error": "No skills could be extracted from the job description."
                }
            }

        # Exact matches only.
        strong_skills = final_jd_set.intersection(final_resume_set)

        # JD skills that are not directly present.
        missing_candidates = final_jd_set.difference(final_resume_set)

        # Related technology mapping.
        related_skills = set()
        for jd_skill in missing_candidates:
            related = self.normalizer.get_related_skills(jd_skill)
            if related.intersection(final_resume_set):
                related_skills.update(related.intersection(final_resume_set))

        # Missing means genuinely not present AND no related technology exists.
        missing_skills = {
            skill
            for skill in missing_candidates
            if not self.normalizer.get_related_skills(skill).intersection(final_resume_set)
        }

        # Resume-only recognized skills.
        known_canonical_skills = set(self.normalizer.alias_matrix.values())
        additional_skills = {
            skill
            for skill in final_resume_set.difference(final_jd_set)
            if skill in known_canonical_skills
        }

        # Score ONLY exact matches.
        required_count = len(final_jd_set)
        raw_score = (len(strong_skills) / required_count) * 25.0 if required_count > 0 else 0.0
        total_score = round(min(raw_score, 25.0), 1)

        return {
            "totalScore": total_score,
            "maxScore": 25.0,
            "skillsAnalysis": {
                "strongSkills": sorted(self._display_skills(strong_skills)),
                "missingSkills": sorted(self._display_skills(missing_skills)),
                "relatedSkills": sorted(self._display_skills(related_skills)),
                "additionalSkills": sorted(self._display_skills(additional_skills))
            }
        }

    def _extract_and_normalize(
        self,
        explicit_list: Optional[List[str]],
        raw_text: str
    ) -> Set[str]:

        normalized_set: Set[str] = set()

        # 1. Explicit parsed skill list
        if explicit_list and isinstance(explicit_list, list):
            for skill in explicit_list:
                canonical = self.normalizer.normalize(skill)
                if canonical:
                    normalized_set.add(canonical)

        # 2. Raw text fallback scan with symbol-safe boundaries
        if raw_text and isinstance(raw_text, str):
            text_lower = raw_text.lower()

            # Sort longer aliases first (e.g. "rest api" before "rest")
            aliases = sorted(
                self.normalizer.alias_matrix.items(),
                key=lambda item: len(item[0]),
                reverse=True
            )

            for phrase, canonical_token in aliases:
                if not phrase:
                    continue

                escaped_phrase = re.escape(phrase.lower())

                # Symbol-safe boundary: Works for C++, .NET, C#, Go, TS, JS
                pattern = (
                    r'(?:^|(?<=[^a-zA-Z0-9_]))' +
                    escaped_phrase +
                    r'(?:$|(?=[^a-zA-Z0-9_]))'
                )

                if re.search(pattern, text_lower):
                    normalized_set.add(canonical_token)

        return normalized_set

    def _display_skills(self, skills: Set[str]) -> List[str]:
        return [
            self.normalizer.get_display_name(skill)
            for skill in skills
        ]