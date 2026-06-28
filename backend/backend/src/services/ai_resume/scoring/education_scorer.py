import re
from typing import Dict, Any, List

class EducationScorer:
    """
    Deterministic Education Scorer for the ATS Engine.
    Evaluates parsed education records against a strict 10-point rubric:
    - Institution Name Presence: 3 points
    - Duration/Dates Presence: 3 points
    - CGPA/Percentage Validity: 4 points
    No AI or LLM lookups. Pure deterministic check.
    """

    def __init__(self):
        # Regex to validate standard CGPA (e.g., 8.5, 3.8/4, 9.2/10) or Percentages (e.g., 85%, 78 %)
        self.cgpa_pattern = re.compile(r'\b(?:\d{1,2}\.\d{1,2}(?:\s*/\s*\d{1,2})?|\d{2,3}\s*%)\b')
        
        # Regex to filter out empty date strings or placeholders
        self.date_pattern = re.compile(r'\b(?:19|20)\d{2}\b|\b(?:present|current)\b', re.IGNORECASE)

    def score_education(self, education_records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Scores the candidate's education section. 
        Iterates over all records and rewards points if *any* valid baseline entry exists 
        (e.g., if their B.Tech entry has a CGPA but their High School entry doesn't, they still get the points).
        
        :param education_records: List of dictionaries extracted by the parser.
               Expected shape per item: {
                   "institution": "University Name" or None,
                   "duration": "2020 - 2024" or None,
                   "grade": "8.7 CGPA" or "82%" or None
               }
        :return: Structured report map with exact point breakups.
        """
        has_institution = False
        has_duration = False
        has_cgpa = False

        # Details of matched records for frontend feedback transparency
        detected_institution = "Missing"
        detected_duration = "Missing"
        detected_cgpa = "Missing"

        # Safe fallback if payload is missing or not a list
        if not education_records or not isinstance(education_records, list):
            return self._build_response(0, 0, 0, "Missing", "Missing", "Missing")

        # Process each record deterministically
        for record in education_records:
            inst = record.get("institution")
            dur = record.get("duration")
            grade = record.get("grade")

            # 1. Check Institution (Must be a non-empty string with substance)
            if inst and isinstance(inst, str) and len(inst.strip()) > 4:
                has_institution = True
                detected_institution = inst.strip()

            # 2. Check Duration (Must contain a year like 2022 or 'Present')
            if dur and isinstance(dur, str) and self.date_pattern.search(dur):
                has_duration = True
                detected_duration = dur.strip()

            # 3. Check CGPA / Percentage validity via strict regex match
            if grade and isinstance(grade, str) and self.cgpa_pattern.search(grade):
                has_cgpa = True
                detected_cgpa = grade.strip()

        # Calculate exact score layers based on absolute flags
        institution_score = 3 if has_institution else 0
        duration_score = 3 if has_duration else 0
        cgpa_score = 4 if has_cgpa else 0
        
        total_score = institution_score + duration_score + cgpa_score

        return self._build_response(
            total_score, 
            institution_score, 
            duration_score, 
            cgpa_score,
            detected_institution, 
            detected_duration, 
            detected_cgpa
        )

    def _build_response(self, total: int, inst_s: int, dur_s: int, grade_s: int, 
                        inst_txt: str, dur_txt: str, grade_txt: str) -> Dict[str, Any]:
        """Helper to output a highly structured, scannable dictionary for the final ATS Report."""
        return {
            "totalScore": total,
            "maxScore": 10,
            "educationAnalysis": {
                "institution": {
                    "present": inst_s > 0,
                    "score": inst_s,
                    "maxScore": 3,
                    "detectedValue": inst_txt,
                    "feedback": "Institution clear and verified" if inst_s > 0 else "No recognized academic institution listed"
                },
                "duration": {
                    "present": dur_s > 0,
                    "score": dur_s,
                    "maxScore": 3,
                    "detectedValue": dur_txt,
                    "feedback": "Timeline/Graduation year clear" if dur_s > 0 else "Missing graduation year or operational timeline"
                },
                "grade": {
                    "present": grade_s > 0,
                    "score": grade_s,
                    "maxScore": 4,
                    "detectedValue": grade_txt,
                    "feedback": "CGPA or Percentage accurately documented" if grade_s > 0 else "Missing academic performance metric (CGPA/Percentage)"
                }
            }
        }