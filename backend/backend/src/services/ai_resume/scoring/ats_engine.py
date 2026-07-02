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
        project_score_out_of_40 = project_report.get("totalScore", 0)

        # 5. Run Experience/Project Weight Allocation Matching
        experience_report = self.experience_scorer.score_experience(
            experience_records=parsed_experience,
            project_score_out_of_40=project_score_out_of_40,
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


        # Matches patterns like: +91 9618211626, 9912081886, +91-98765-43210, +1 (555) 019-2834
        phone_regex = re.compile(
            r'(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b|\b\d{10,12}\b'
        )
        
        # Scrape and audit raw resume text buffer
        raw_text_strip = raw_resume_text.strip()
        text_lower = raw_resume_text.lower()
        
        # Execute structural regex search loop
        has_phone = bool(phone_regex.search(raw_text_strip))
        # has_phone = any(char.isdigit() for char in text_lower) and len([c for c in text_lower if c.isdigit()]) >= 10
        # if "phone" in text_lower or "contact" in text_lower or "+" in text_lower:
        #     has_phone = True
        has_email = "@" in text_lower and "." in text_lower

        master_report["hygieneCheck"]["phoneRegexMatch"] = has_phone
        master_report["hygieneCheck"]["hasPhone"] = has_phone
        master_report["hygieneCheck"]["hasEmail"] = has_email
        # master_report["track"] = track_determined
        # Inject metadata parameters for downstream contextual components
        master_report["track"] = track_determined
        return master_report