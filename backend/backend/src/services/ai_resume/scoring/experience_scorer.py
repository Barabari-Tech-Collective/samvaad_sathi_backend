import re
from typing import Dict, Any, List

class ExperienceScorer:
    """
    Deterministic Experience Scorer for the ATS Engine.
    Applies a dynamic weighting matrix based on the candidate's experience level:
    - Fresher:     Corporate Experience = 0%,  Projects = 40%
    - Mid-Level:   Corporate Experience = 20%, Projects = 20%
    - Experienced: Corporate Experience = 35%, Projects = 5%
    
    Ensures the combined maximum contribution from Experience + Projects stays exactly at 40%.
    Pure Python logic. 100% deterministic.
    """

    def __init__(self):
        # Regex to parse out timeline years or month duration details from text descriptions
        self.year_pattern = re.compile(r'\b(\d+)\s*(?:yr|year)', re.IGNORECASE)

    def calculate_weights(self, experience_level: str, raw_resume_text: str = "") -> Dict[str, float]:
        """
        Determines the target weight distribution strategy based on profile data tags.
        """
        level = experience_level.lower().strip()
        
        # 1. Heuristically infer experience level if it's passed as ambiguous or empty
        if not level or level in ["auto", "detect", "unknown"]:
            level = self._infer_level_from_text(raw_resume_text)

        # 2. Map structural matrices explicitly based on your rules
        if any(k in level for k in ["fresher", "intern", "entry", "0 years"]):
            return {
                "level": "fresher",
                "experience_weight": 0.0,
                "project_weight": 40.0
            }
        elif any(k in level for k in ["senior", "lead", "experienced", "expert", "5+"]):
            return {
                "level": "experienced",
                "experience_weight": 35.0,
                "project_weight": 5.0
            }
        else:
            # Default fallback category: Mid-Level profile tracking matrix
            return {
                "level": "mid",
                "experience_weight": 20.0,
                "project_weight": 20.0
            }

    def score_experience(self, experience_records: List[Dict[str, Any]], 
                         project_score_out_of_40: float, 
                         experience_level: str, 
                         raw_resume_text: str = "") -> Dict[str, Any]:
        """
        Calculates the weighted structural scores for experience and projects.
        
        :param experience_records: Array of parsed corporate history nodes from the resume
        :param project_score_out_of_40: The calculated score returned directly from ProjectScorer
        :param experience_level: The input experience level string from the user profile
        :param raw_resume_text: Backup raw string buffer
        :return: Standardized dynamic score mapping payload
        """
        # 1. Fetch dynamic matrix weights
        weights = self.calculate_weights(experience_level, raw_resume_text)
        level_detected = weights["level"]
        exp_weight = weights["experience_weight"]
        proj_weight = weights["project_weight"]

        # 2. Evaluate base corporate history durability (Out of 100 base index points)
        base_experience_index = self._calculate_base_experience_index(experience_records)

        # 3. Scale values down according to the structural allocation matrix
        # Normalized Experience Score = (Base index % of allocation weight)
        calculated_exp_score = round((base_experience_index / 100.0) * exp_weight, 1)
        
        # Normalized Project Score = (Project performance ratio * project allocation weight)
        calculated_proj_score = round((project_score_out_of_40 / 40.0) * proj_weight, 1)

        # Combined tracking block totals
        combined_score = round(calculated_exp_score + calculated_proj_score, 1)
        max_combined_score = int(exp_weight + proj_weight) # Always structurally sums up to 40

        return {
            "detectedLevel": level_detected,
            "totalScore": combined_score,
            "maxScore": max_combined_score,
            "matrixAllocation": {
                "experienceWeight": exp_weight,
                "projectWeight": proj_weight
            },
            "experienceAnalysis": {
                "corporateHistoryScore": calculated_exp_score,
                "projectContributionScore": calculated_proj_score,
                "recordCount": len(experience_records),
                "feedback": self._generate_feedback(level_detected, len(experience_records), base_experience_index)
            }
        }

    def _infer_level_from_text(self, text: str) -> str:
        """Heuristically inspects text data patterns for explicit year mentions."""
        matches = self.year_pattern.findall(text)
        if matches:
            years = max([int(m) for m in matches])
            if years >= 5: return "experienced"
            if years >= 1: return "mid"
        return "fresher"

    def _calculate_base_experience_index(self, records: List[Dict[str, Any]]) -> float:
        """
        Calculates a structural base performance rating index (0-100) 
        by inspecting the density of corporate roles listed.
        """
        if not records or not isinstance(records, list):
            return 0.0
            
        count = len(records)
        # 1 record = 50 points, 2 records = 85 points, 3+ records = 100 points
        if count == 1:
            base_index = 50.0
        elif count == 2:
            base_index = 85.0
        else:
            base_index = 100.0

        # Run bullet point quality validation audit check
        # Deduct if records have weak summaries or are empty shells
        for role in records:
            highlights = role.get("highlights", [])
            if not highlights or len(highlights) < 2:
                base_index -= 10.0 # Deduct points for poor description quality

        return max(0.0, min(base_index, 100.0))

    def _generate_feedback(self, level: str, record_count: int, index: float) -> str:
        """Generates clear structured textual feedback for rendering."""
        if level == "fresher":
            return "Fresher profile configuration applied. Scoring focus shifted heavily onto project metrics and academic builds."
        if record_count == 0:
            return "Critical corporate alignment issue: No occupational records detected for professional level track."
        if index < 70:
            return "Occupational alignment verified, but experience description density is low. Expand bullet points with technical impacts."
        return "Professional track history shows solid distribution and structure."