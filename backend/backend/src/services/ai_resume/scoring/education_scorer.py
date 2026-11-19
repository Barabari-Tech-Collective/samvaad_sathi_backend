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
        # Regex to validate standard CGPA (e.g., 8.5, 3.8/4, 9.2/10) or Percentages (e.g., 85%, 78 %, 84.5%)
        self.cgpa_pattern = re.compile(
            r'\b(?:\d{1,2}(?:\.\d{1,2})?\s*/\s*(?:10(?:\.0)?|4(?:\.0)?|100)|\d{1,2}\.\d{1,2}(?:\s*/\s*\d{1,2})?|\d{2}(?:\.\d{1,2})?\s*%)\b',
            re.IGNORECASE,
        )
        
        # Regex to filter out empty date strings or placeholders
        self.date_pattern = re.compile(r'\b(?:19|20)\d{2}\b|\b(?:present|current)\b', re.IGNORECASE)

    def _extract_cgpa_from_text(self, text: str) -> str | None:
        """
        Extracts CGPA, GPA, CPI, or percentage from raw or structured text.
        Returns a clean formatted string representation or None.
        """
        if not text or not isinstance(text, str):
            return None

        # 1. Look for explicit keyword patterns first: "CGPA: 8.7", "GPA: 3.9/4.0", "Percentage: 84.5%", "Aggregate: 78%"
        keyword_match = re.search(
            r'(?i)\b(?:cgpa|cpi|sgpa|gpa|percentage|percent|aggregate|marks?|grade)\s*[:\-–=]?\s*'
            r'(\d{1,2}(?:\.\d{1,2})?\s*(?:/\s*(?:10(?:\.0)?|4(?:\.0)?|100))?|\d{2}(?:\.\d{1,2})?\s*%)',
            text,
        )
        if keyword_match:
            return keyword_match.group(0).strip()

        # 2. Look for suffix patterns: "8.7 CGPA", "8.7/10 CGPA", "85% marks"
        suffix_match = re.search(
            r'(?i)\b(\d{1,2}(?:\.\d{1,2})?\s*(?:/\s*(?:10(?:\.0)?|4(?:\.0)?))?\s*(?:cgpa|cpi|sgpa|gpa))\b',
            text,
        )
        if suffix_match:
            return suffix_match.group(0).strip()

        # 3. Look for explicit denominator patterns: "8.5/10", "3.8/4.0", "9/10"
        denom_match = re.search(
            r'\b(\d{1,2}(?:\.\d{1,2})?\s*/\s*(?:10(?:\.0)?|4(?:\.0)?))\b',
            text,
        )
        if denom_match:
            return denom_match.group(0).strip()

        # 4. Standalone percentage: "85%" or "85.4%"
        pct_match = re.search(r'\b(\d{2}(?:\.\d{1,2})?\s*%)\b', text)
        if pct_match:
            return pct_match.group(0).strip()

        # 5. Generic decimal match (e.g. when text is already just a number like "8.5")
        gen_match = self.cgpa_pattern.search(text)
        if gen_match:
            return gen_match.group(0).strip()

        return None

    def score_education(
        self,
        education_records: List[Dict[str, Any]],
        raw_resume_text: str = "",
    ) -> Dict[str, Any]:
        """
        Scores the candidate's education section. 
        Iterates over all records and rewards points if *any* valid baseline entry exists 
        (e.g., if their B.Tech entry has a CGPA but their High School entry doesn't, they still get the points).
        Falls back to extracting CGPA/percentage from raw_resume_text if omitted from parsed records.
        
        :param education_records: List of dictionaries extracted by the parser.
               Expected shape per item: {
                   "institution": "University Name" or None,
                   "duration": "2020 - 2024" or None,
                   "grade": "8.7 CGPA" or "82%" or None
               }
        :param raw_resume_text: Optional unparsed resume text for fallback extraction.
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
        records = education_records if isinstance(education_records, list) else []

        # Process each record deterministically
        for record in records:
            if not isinstance(record, dict):
                continue

            inst = record.get("institution")
            dur = record.get("duration")

            # 1. Check Institution (Must be a non-empty string with substance)
            if inst and isinstance(inst, str) and len(inst.strip()) > 4:
                has_institution = True
                detected_institution = inst.strip()

            # 2. Check Duration (Must contain a year like 2022 or 'Present')
            if dur and isinstance(dur, str) and self.date_pattern.search(dur):
                has_duration = True
                detected_duration = dur.strip()

            # 3. Check CGPA / Percentage validity across multiple possible schema fields
            for key in ("grade", "cgpa", "gpa", "cpi", "sgpa", "percentage", "percent", "marks", "score", "result"):
                raw_val = record.get(key)
                if raw_val is not None:
                    extracted = self._extract_cgpa_from_text(str(raw_val))
                    if extracted:
                        has_cgpa = True
                        detected_cgpa = extracted
                        record["grade"] = detected_cgpa
                        break

            # 4. If not found in explicit keys, check degree/field/description strings
            if not has_cgpa:
                for key in ("degree", "field", "description", "details"):
                    aux_val = record.get(key)
                    if aux_val and isinstance(aux_val, str):
                        extracted = self._extract_cgpa_from_text(aux_val)
                        if extracted:
                            has_cgpa = True
                            detected_cgpa = extracted
                            record["grade"] = detected_cgpa
                            break

        # Fallback 5: If CGPA was not captured in parsed structured records, check raw_resume_text
        if not has_cgpa and raw_resume_text and isinstance(raw_resume_text, str):
            # Prioritize searching an education section
            edu_section_match = re.search(
                r'(?i)(?:education|academic|qualifications?)([\s\S]{1,1500}?)(?:experience|employment|projects?|skills?|certifications?|achievements?|$)',
                raw_resume_text,
            )
            if edu_section_match:
                extracted = self._extract_cgpa_from_text(edu_section_match.group(1))
                if extracted:
                    has_cgpa = True
                    detected_cgpa = extracted

            # If still not found, check anchored CGPA keywords across full resume text
            if not has_cgpa:
                kw_match = re.search(
                    r'(?i)\b(?:cgpa|cpi|sgpa|gpa|percentage|percent|aggregate)\s*[:\-–=]?\s*'
                    r'(\d{1,2}(?:\.\d{1,2})?\s*(?:/\s*(?:10(?:\.0)?|4(?:\.0)?|100))?|\d{2}(?:\.\d{1,2})?\s*%)',
                    raw_resume_text,
                )
                if kw_match:
                    has_cgpa = True
                    detected_cgpa = kw_match.group(0).strip()
                else:
                    suffix_match = re.search(
                        r'(?i)\b(\d{1,2}(?:\.\d{1,2})?\s*(?:/\s*(?:10(?:\.0)?|4(?:\.0)?))?\s*(?:cgpa|cpi|sgpa|gpa))\b',
                        raw_resume_text,
                    )
                    if suffix_match:
                        has_cgpa = True
                        detected_cgpa = suffix_match.group(0).strip()

            # If found from raw text, backfill into the first record so evidence display captures it
            if has_cgpa and records and isinstance(records[0], dict):
                records[0]["grade"] = detected_cgpa

        # Fallback 6: If institution or duration is missing and records was empty, check raw text
        if not has_institution and raw_resume_text and isinstance(raw_resume_text, str):
            inst_match = re.search(
                r'(?i)\b([A-Z][A-Za-z\s&]{4,60}(?:University|Institute|College|Academy|School))\b',
                raw_resume_text,
            )
            if inst_match:
                has_institution = True
                detected_institution = inst_match.group(1).strip()

        if not has_duration and raw_resume_text and isinstance(raw_resume_text, str):
            dur_match = re.search(r'\b((?:19|20)\d{2}\s*(?:-|to|–)\s*(?:(?:19|20)\d{2}|present|current))\b', raw_resume_text, re.IGNORECASE)
            if dur_match:
                has_duration = True
                detected_duration = dur_match.group(1).strip()

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