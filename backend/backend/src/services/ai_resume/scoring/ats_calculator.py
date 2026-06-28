from typing import Dict, Any

class ATSCalculator:
    """
    Deterministic Final ATS Score Aggregator.
    Combines independent sub-system scores directly using 100% pure Python math:
    - Links Analysis:     Max 40 points
    - Skills Analysis:    Max 10 points
    - Education Analysis: Max 10 points
    - Experience/Projects: Max 40 points
    --------------------------------------
    Total ATS Score:     Max 100 points
    
    Ensures zero decimal leakage, sets explicit performance flags, and structures 
    the final report template for both the frontend client and OpenAI context ingestion.
    """

    def __init__(self):
        pass

    def calculate_final_ats_score(self, link_report: Dict[str, Any], 
                                  skills_report: Dict[str, Any], 
                                  education_report: Dict[str, Any], 
                                  experience_report: Dict[str, Any],
                                  project_report: Dict[str, Any],
                                  raw_resume_text: str = "", ) -> Dict[str, Any]:
        """
        Gathers independent structural points, applies consolidation math, and maps 
        the overall performance index.
        
        :param link_report: Standard dictionary emitted by LinkScorer
        :param skills_report: Standard dictionary emitted by SkillsScorer
        :param education_report: Standard dictionary emitted by EducationScorer
        :param experience_report: Standard dictionary emitted by ExperienceScorer
        :param raw_resume_text: The raw text of the resume for additional analysis
        :return: High-fidelity, production-grade master ATS scoring report
        """
        # 1. Direct Extraction of Computed Sub-System Values
        links_score = float(link_report.get("totalScore", 0.0))
        skills_score = float(skills_report.get("totalScore", 0.0))
        education_score = float(education_report.get("totalScore", 0.0))
        experience_combined_score = float(experience_report.get("totalScore", 0.0))

        # 2. Grand Aggregation (Normalized to a clean, rounded integer out of 100)
        raw_grand_total = links_score + skills_score + education_score + experience_combined_score
        final_ats_score = int(round(min(max(raw_grand_total, 0.0), 100.0)))

        # 3. Categorize Global Performance Rank
        rating_label, threshold_color = self._determine_score_brackets(final_ats_score)

        # 4. Generate Core Engineering Quality Check Flag Maps (Hygiene Snapshot)
        # Synthesizes cross-module attributes into a flat boolean map for rapid frontend checks
        hygiene_snapshot = self._generate_hygiene_snapshot(link_report, education_report, raw_resume_text)
        

        # 5. Build Master Composite Production Payload
        return {
            "atsScore": final_ats_score,
            "maxScore": 100,
            "overallRating": rating_label,
            "uiThemeColor": threshold_color,
            "scoreBreakdown": {
                "skillsMatch": int(round(skills_score * 10)),      # Scales 0-10 base to 0-100 index for React view
                "experienceMatch": int(round(experience_combined_score * 2.5)), # Scales 0-40 base to 0-100 index for React view
                "formattingScore": int(round(links_score * 2.5)),  # Scales 0-40 base to 0-100 index for React view
                "keywordDensity": int(round(education_score * 10)) # Scales 0-10 base to 0-100 index for React view
            },
            "deterministicMetrics": {
                "linksModule": link_report,
                "skillsModule": skills_report,
                "educationModule": education_report,
                "experienceModule": experience_report,
                "projectModule": project_report
            },
            "hygieneCheck": hygiene_snapshot
        }

    def _determine_score_brackets(self, score: int) -> tuple:
        """Determines tracking descriptions and status colors based on strict score thresholds."""
        if score >= 80:
            return "Excellent", "green"
        elif score >= 65:
            return "Good", "blue"
        elif score >= 45:
            return "Average", "yellow"
        else:
            return "Needs Improvement", "red"
        

    def _generate_hygiene_snapshot(self, link_report: Dict[str, Any], education_report: Dict[str, Any], raw_resume_text: str = "") -> Dict[str, bool]:
        """
        Flattens multi-layer scoring flags into a centralized data layer 
        and scans raw text dynamically for contact primitives.
        """
        analysis = link_report.get("linkAnalysis", {})
        edu_analysis = education_report.get("educationAnalysis", {})

        # Extract LinkedIn flags
        has_linkedin = False
        linkedin_working = False
        if "linkedIn" in analysis:
            has_linkedin = analysis["linkedIn"].get("present", False)
            linkedin_working = analysis["linkedIn"].get("working", False)

        # Extract GitHub flags
        has_github = False
        github_working = False
        if "github" in analysis:
            has_github = analysis["github"].get("present", False)
            github_working = analysis["github"].get("working", False)
        elif "designPlatform" in analysis:
            has_github = analysis["designPlatform"].get("present", False)
            github_working = analysis["designPlatform"].get("working", False)

        # Extract Portfolio flags
        has_portfolio = False
        portfolio_working = False
        portfolio_node = analysis.get("projects") or analysis.get("livePortfolio")
        if portfolio_node:
            has_portfolio = portfolio_node.get("present", False)
            portfolio_working = portfolio_node.get("working", False)

        # do not flag a standalone custom portfolio as true.
        if link_report.get("track") == "engineering" and not analysis.get("portfolio"):
            # If no independent domain URL was categorized outside git/linkedin by your flat_map, force false
            has_portfolio = False
            portfolio_working = False
        # Evaluate basic contact strings inside the resume context dump safely
        text_lower = raw_resume_text.lower()
        
        has_phone = any(char.isdigit() for char in text_lower) and len([c for c in text_lower if c.isdigit()]) >= 10
        if "phone" in text_lower or "contact" in text_lower or "+" in text_lower:
            has_phone = True
            
        has_email = "@" in text_lower and "." in text_lower

        return {
            "hasLinkedIn": has_linkedin,
            "linkedInWorking": linkedin_working,
            "hasGithub": has_github,
            "githubWorking": github_working,
            "hasPortfolio": has_portfolio,
            "portfolioWorking": portfolio_working,
            "hasInstitution": edu_analysis.get("institution", {}).get("present", False),
            "hasDuration": edu_analysis.get("duration", {}).get("present", False),
            "hasScore": edu_analysis.get("grade", {}).get("present", False),
            "hasPhone": has_phone,   
            "hasEmail": has_email    
        }