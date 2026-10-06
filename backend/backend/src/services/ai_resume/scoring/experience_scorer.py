import re
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime


class ExperienceScorer:
    """
    Production-Grade Experience Scorer for the ATS Engine.
    Evaluates corporate experience by merging overlapping time intervals,
    parsing multi-format date strings (Naukri, LinkedIn, Canva), and cleanly 
    separating candidate seniority from target job weighting matrixes.
    """

    def __init__(self):
        self.month_names = {
            "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
            "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
            "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
            "nov": 11, "november": 11, "dec": 12, "december": 12
        }

    def _parse_months_from_string(self, duration_str: str) -> int:
        """Helper to parse raw duration text strings like '1 yr 6 mos' or '18 months'."""
        if not duration_str or not isinstance(duration_str, str):
            return 0

        text = duration_str.lower().strip()
        total_months = 0

        # Match years
        years_match = re.search(r'(\d+)\s*(?:yrs?|years?)', text)
        if years_match:
            total_months += int(years_match.group(1)) * 12

        # Match months
        months_match = re.search(r'(\d+)\s*(?:mos?|months?)', text)
        if months_match:
            total_months += int(months_match.group(1))

        return total_months

    def _extract_date(self, date_str: str) -> Optional[datetime]:
        """
        Parses date strings safely into datetime objects.
        Supports '06/2024', '08-2023', '2021', 'Jan 2021', 'August 2023'.
        Returns None if no valid year is detected.
        """
        if not date_str or not isinstance(date_str, str):
            return None

        clean_str = date_str.lower().strip()

        # 1. Check numeric MM/YYYY or MM-YYYY first
        numeric = re.search(r'\b(\d{1,2})[/-](\d{4})\b', clean_str)
        if numeric:
            month = int(numeric.group(1))
            year = int(numeric.group(2))
            if 1 <= month <= 12:
                return datetime(year, month, 1)

        # 2. Check year search
        years = re.findall(r'\b(19\d\d|20\d\d)\b', clean_str)
        if not years:
            return None

        year = int(years[0])
        month = 1

        # 3. Check month names
        for name, value in self.month_names.items():
            if name in clean_str:
                month = value
                break

        return datetime(year, month, 1)

    def _parse_duration_range(self, duration: str) -> Tuple[Optional[datetime], Optional[datetime]]:
        """
        Parses multi-format duration range strings:
        - 'Aug 2023 - Present'
        - 'August 2023 – Present'
        - 'Jan 2022 - Dec 2022'
        - '2021 - 2023'
        """
        if not duration or not isinstance(duration, str):
            return None, None

        duration_clean = duration.strip()
        parts = re.split(r'\s*[-–—]\s*', duration_clean)

        if len(parts) != 2:
            return None, None

        start_raw = parts[0].strip()
        end_raw = parts[1].strip()

        try:
            start_dt = self._extract_date(start_raw)
            if not start_dt:
                return None, None

            if any(x in end_raw.lower() for x in ["present", "current", "now"]):
                end_dt = datetime.now()
            else:
                end_dt = self._extract_date(end_raw)

            return start_dt, end_dt
        except Exception:
            return None, None

    def _merge_overlapping_intervals(self, intervals: List[Tuple[datetime, datetime]]) -> int:
        """
        Merges overlapping time intervals and returns total non-overlapping months.
        Example: [Jan 2023 - Present] + [Aug 2023 - Present] -> [Jan 2023 - Present]
        """
        if not intervals:
            return 0

        # Sort intervals by start date
        sorted_intervals = sorted(intervals, key=lambda x: x[0])
        merged: List[Tuple[datetime, datetime]] = []

        for current in sorted_intervals:
            if not merged:
                merged.append(current)
            else:
                prev_start, prev_end = merged[-1]
                curr_start, curr_end = current

                if curr_start <= prev_end:
                    # Overlap detected! Extend end date to maximum range
                    new_end = max(prev_end, curr_end)
                    merged[-1] = (prev_start, new_end)
                else:
                    merged.append(current)

        # Sum total unique months across merged intervals
        total_months = 0
        for start_dt, end_dt in merged:
            diff_months = (end_dt.year - start_dt.year) * 12 + (end_dt.month - start_dt.month) + 1
            total_months += max(1, diff_months)

        return total_months

    def _calculate_total_months(self, records: List[Dict[str, Any]]) -> int:
        """
        Calculates total unique experience months across records by prioritizing 
        date ranges first, then explicit string fallbacks, and merging overlapping intervals.
        """
        print("\n--- [EXPERIENCE SCORER: ROLE-BY-ROLE DURATION LOGS] ---")
        if not records or not isinstance(records, list):
            print("No structured experience records found in parsed JSON.")
            return 0

        date_intervals: List[Tuple[datetime, datetime]] = []
        fallback_explicit_months = 0

        for idx, role in enumerate(records):
            title = role.get("title", role.get("role", "Unknown Role"))
            company = role.get("company", role.get("companyName", "Unknown Company"))
            raw_duration = str(role.get("duration", "")).strip()
            start_date_str = str(role.get("startDate", "")).strip()
            end_date_str = str(role.get("endDate", "")).strip()

            # PRIORITY 1: Range in duration string (e.g., "Aug 2023 - Present", "06/2022 - 08/2023")
            start_dt, end_dt = self._parse_duration_range(raw_duration)
            if start_dt and end_dt:
                date_intervals.append((start_dt, end_dt))
                diff = (end_dt.year - start_dt.year) * 12 + (end_dt.month - start_dt.month) + 1
                print(f"  [Role {idx + 1}] '{title}' at '{company}' -> Date Range in duration string parsed: '{raw_duration}' ({diff} mos)")
                continue

            # PRIORITY 2: Explicit startDate / endDate keys
            if start_date_str:
                start_dt = self._extract_date(start_date_str)
                if start_dt:
                    if not end_date_str or any(x in end_date_str.lower() for x in ["present", "current", "now"]):
                        end_dt = datetime.now()
                    else:
                        end_dt = self._extract_date(end_date_str) or datetime.now()

                    date_intervals.append((start_dt, end_dt))
                    diff = (end_dt.year - start_dt.year) * 12 + (end_dt.month - start_dt.month) + 1
                    print(f"  [Role {idx + 1}] '{title}' at '{company}' -> Start/End keys parsed: '{start_date_str}' to '{end_date_str}' ({diff} mos)")
                    continue

            # PRIORITY 3: Fallback to explicit duration string ONLY if no date range was found
            parsed_months = self._parse_months_from_string(raw_duration)
            if parsed_months > 0:
                fallback_explicit_months += parsed_months
                print(f"  [Role {idx + 1}] '{title}' at '{company}' -> Explicit duration string parsed fallback: '{raw_duration}' ({parsed_months} mos)")
                continue

            print(f"  [Role {idx + 1}] '{title}' at '{company}' -> Could not parse duration/dates. 0 months added.")

        # Compute merged unique interval months
        merged_months = self._merge_overlapping_intervals(date_intervals)
        total_experience_months = merged_months + fallback_explicit_months

        print(f"Merged Unique Experience Months: {total_experience_months} months\n")

        return total_experience_months
    def _calculate_base_experience_index(self, total_months: int) -> float:
        """
        Maps total months directly to non-punitive base index tiers.
        0 months       -> 0.0
        1-3 months     -> 20.0
        4-6 months     -> 35.0
        7-12 months    -> 50.0
        13-24 months   -> 70.0
        25-48 months   -> 85.0
        48+ months     -> 100.0
        """
        if total_months <= 0:
            return 0.0
        elif total_months <= 3:
            return 20.0
        elif total_months <= 6:
            return 35.0
        elif total_months <= 12:
            return 50.0
        elif total_months <= 24:
            return 70.0
        elif total_months <= 48:
            return 85.0
        else:
            return 100.0

    def _infer_candidate_seniority(self, total_months: int) -> str:
        """Computes true Candidate Profile Seniority based solely on experience length."""
        if total_months >= 60:
            return "Senior"
        elif total_months >= 24:
            return "Mid-Level"
        elif total_months >= 6:
            return "Junior"
        else:
            return "Fresher"

    def calculate_weights(self, target_experience_level: str) -> Dict[str, Any]:
        """
        Determines evaluation matrix weights based on target job level requirement.
        - Entry/Fresher role: Projects (25.0 pts), Corporate Experience (10.0 pts)
        - Mid role:           Projects (17.5 pts), Corporate Experience (17.5 pts)
        - Senior role:        Projects (10.0 pts), Corporate Experience (25.0 pts)
        """
        level = target_experience_level.lower().strip()

        is_entry = any(k in level for k in ["fresher", "entry", "intern", "0 years", "0-1", "junior"])
        is_senior = any(k in level for k in ["experienced", "senior", "lead", "expert", "5+"])

        if is_entry:
            return {
                "targetTrack": "Entry Level / Fresher",
                "experience_weight": 10.0,
                "project_weight": 25.0
            }
        elif is_senior:
            return {
                "targetTrack": "Senior Level",
                "experience_weight": 25.0,
                "project_weight": 10.0
            }
        else:
            return {
                "targetTrack": "Mid Level",
                "experience_weight": 17.5,
                "project_weight": 17.5
            }

    def score_experience(
        self,
        experience_records: List[Dict[str, Any]],
        project_score_out_of_35: float,
        experience_level: str = "",
        raw_resume_text: str = ""
    ) -> Dict[str, Any]:
        """Calculates experience score using merged intervals and separated level metrics."""
        print(f"\n================ [EXPERIENCE SCORER DEBUG START] ================")
        print(f"Target Job Experience Level String: '{experience_level}'")
        print(f"Input Project Score (Out of 35): {project_score_out_of_35}")

        total_months = self._calculate_total_months(experience_records)
        weights = self.calculate_weights(experience_level)

        target_track = weights["targetTrack"]
        exp_weight = weights["experience_weight"]
        proj_weight = weights["project_weight"]

        # Candidate Seniority computed strictly from total unique experience
        candidate_seniority = self._infer_candidate_seniority(total_months)

        # If the target job is explicitly Entry Level / Fresher, 
        # do not penalize for lack of corporate experience. Give full index.
        if target_track == "Entry Level / Fresher":
            base_experience_index = 100.0
        else:
            base_experience_index = self._calculate_base_experience_index(total_months)

        calculated_exp_score = round((base_experience_index / 100.0) * exp_weight, 1)
        calculated_proj_score = round((project_score_out_of_35 / 35.0) * proj_weight, 1)

        combined_score = round(calculated_exp_score + calculated_proj_score, 1)

        # Format human-readable duration
        years = total_months // 12
        months = total_months % 12
        if years > 0 and months > 0:
            formatted_duration = f"{years} yr{'s' if years > 1 else ''} {months} mo{'s' if months > 1 else ''}"
        elif years > 0:
            formatted_duration = f"{years} yr{'s' if years > 1 else ''}"
        elif months > 0:
            formatted_duration = f"{months} mo{'s' if months > 1 else ''}"
        else:
            formatted_duration = "None listed"

        print(f"Target Job Track: {target_track}")
        print(f"Candidate Seniority Profile: {candidate_seniority}")
        print(f"Merged Unique Duration: {formatted_duration} ({total_months} months)")
        print(f"Base Experience Index (0-100): {base_experience_index}")
        print(f"Allocated Matrix Weights -> Experience: {exp_weight} pts | Projects: {proj_weight} pts")
        print(f"Component Scores -> Corporate Score: {calculated_exp_score}/{exp_weight} | Project Score: {calculated_proj_score}/{proj_weight}")
        print(f"Final Combined Experience Module Score: {combined_score}/35.0")
        print(f"================ [EXPERIENCE SCORER DEBUG END] ================\n")

        return {
            "detectedLevel": candidate_seniority,
            "targetTrack": target_track,
            "totalScore": combined_score,
            "maxScore": 35.0,
            "totalMonths": total_months,
            "formattedDuration": formatted_duration,
            "matrixAllocation": {
                "experienceWeight": exp_weight,
                "projectWeight": proj_weight
            },
            "experienceAnalysis": {
                "corporateHistoryScore": calculated_exp_score,
                "projectContributionScore": calculated_proj_score,
                "recordCount": len(experience_records),
                "totalExperienceFormatted": formatted_duration,
                "candidateSeniority": candidate_seniority,
                "targetJobTrack": target_track,
                "professionalTimeline": "Consistent" if total_months >= 12 else "Early Career",
                "careerTrack": "Stable" if len(experience_records) > 0 else "Project Focused",
                "feedback": f"Total Experience: {formatted_duration} | Candidate Seniority: {candidate_seniority} | Target Track: {target_track}"
            }
        }