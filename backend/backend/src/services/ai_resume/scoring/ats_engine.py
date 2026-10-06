import re
from typing import Dict, Any, List
from src.services.ai_resume.scoring.link_scorer import LinkScorer
from src.services.ai_resume.scoring.skills_scorer import SkillsScorer
from src.services.ai_resume.scoring.education_scorer import EducationScorer
from src.services.ai_resume.scoring.project_scorer import ProjectScorer
from src.services.ai_resume.scoring.experience_scorer import ExperienceScorer
from src.services.ai_resume.scoring.ats_calculator import ATSCalculator

class ATSEngine:
    """
    The central orchestrator for the Deterministic ATS scoring pipeline.
    Coordinates data extraction, passes execution blocks to independent scorers,
    and runs the final calculation matrix.
    100% Deterministic Python execution.
    """
    
    def __init__(self):
        self.link_scorer = LinkScorer()
        self.skills_scorer = SkillsScorer()
        self.education_scorer = EducationScorer()
        self.project_scorer = ProjectScorer()
        self.experience_scorer = ExperienceScorer()
        self.calculator = ATSCalculator()

    def run_assessment(self, 
                       verified_links_raw: Dict[str, Any], 
                       parsed_skills: List[str], 
                       jd_skills: List[str],
                       parsed_education: List[Dict[str, Any]], 
                       parsed_projects: List[Dict[str, Any]], 
                       parsed_experience: List[Dict[str, Any]], 
                       experience_level: str,
                       target_role: str,
                       raw_resume_text: str = "", 
                       raw_jd_text: str = "") -> Dict[str, Any]:
        """
        Runs the complete deterministic assessment across all subsystems.
        """
        # 1. Run Link Scoring (Ingests raw output from your SmartLinkValidator)
        link_report = self.link_scorer.score_links(
          validator_output=verified_links_raw,
          mapped_projects=parsed_projects,
          job_title=target_role
        )
        track_determined = link_report.get("track", "engineering")

        # 2. Run Skills Matching
        skills_report = self.skills_scorer.score_skills(
            resume_skills=parsed_skills, 
            jd_skills=jd_skills, 
            raw_resume_text=raw_resume_text, 
            raw_jd_text=raw_jd_text
        )

        # 3. Run Education Verification
        education_report = self.education_scorer.score_education(parsed_education)

        # 4. Run Project Scoring (Evaluates independent project structural points)
        project_report = self.project_scorer.score_projects(parsed_projects)
        project_score_out_of_35 = project_report.get("totalScore", 0)

        # 5. Run Experience/Project Weight Allocation Matching
        experience_report = self.experience_scorer.score_experience(
            experience_records=parsed_experience,
            project_score_out_of_35=project_score_out_of_35,
            experience_level=experience_level,
            raw_resume_text=raw_resume_text
        )

        # 6. Aggregate into Final Score Report Payload
        master_report = self.calculator.calculate_final_ats_score(
            link_report=link_report,
            skills_report=skills_report,
            education_report=education_report,
            experience_report=experience_report,
            raw_resume_text=raw_resume_text,
            project_report=project_report
        )
        text_lower = raw_resume_text.lower()
        raw_text_strip = raw_resume_text.strip()

        phone_regex = re.compile(r'\+?\d[\d\s\-\(\)]{8,}\d')
        email_regex = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')

        phone_match = phone_regex.search(raw_text_strip)
        email_match = email_regex.search(raw_text_strip)

        digits = ""
        has_phone = False
        if phone_match:
            digits = re.sub(r'\D', '', phone_match.group(0))
            if 10 <= len(digits) <= 15:
                has_phone = True

        has_email = bool(email_match)

        print("phone of Candidate:", phone_match)
        print("email of Candidate:", email_match)

        master_report["hygieneCheck"]["phoneRegexMatch"] = has_phone
        master_report["hygieneCheck"]["hasPhone"] = has_phone
        master_report["hygieneCheck"]["hasEmail"] = has_email
        master_report["track"] = track_determined

        return master_report