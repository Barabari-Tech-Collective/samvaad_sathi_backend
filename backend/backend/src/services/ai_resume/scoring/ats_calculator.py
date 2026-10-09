from typing import Dict, Any, List


class ATSCalculator:
    """
    Deterministic Final ATS Score Aggregator.
    Combines the outputs of the various scoring modules (links, skills, education, experience, projects)
    """

    def __init__(self):
        pass

    def calculate_final_ats_score(
        self,
        link_report: Dict[str, Any],
        skills_report: Dict[str, Any],
        education_report: Dict[str, Any],
        experience_report: Dict[str, Any],
        project_report: Dict[str, Any],
        raw_resume_text: str = ""
    ) -> Dict[str, Any]:
        links_score = float(link_report.get("totalScore", 0.0))
        skills_score = float(skills_report.get("totalScore", 0.0))
        education_score = round((float(education_report.get("totalScore", 0.0)) / 10.0) * 5.0, 1)
        experience_combined_score = float(experience_report.get("totalScore", 0.0))

        raw_grand_total = links_score + skills_score + education_score + experience_combined_score
        final_ats_score = int(round((raw_grand_total / 70.0) * 100.0))
        final_ats_score = min(max(final_ats_score, 0), 100)

        # Calculate project match percentage from project_report (Max 35.0 pts)
        project_total_score = float(project_report.get("totalScore", 0.0))
        project_match_percentage = int(round((project_total_score / 35.0) * 100.0))
        project_match_percentage = min(max(project_match_percentage, 0), 100)

        # Extract total months to detect if candidate is a fresher
        total_months = int(experience_report.get("totalMonths", 0))
        is_fresher = total_months == 0 or experience_report.get("detectedLevel") == "Fresher"
        enriched_projects = self._enrich_project_nodes(project_report, link_report)

        project_report_enriched = {
            **project_report,
            "projectEvaluation": enriched_projects
        }

        hygiene_snapshot = self._generate_hygiene_snapshot(link_report, education_report, raw_resume_text)

        return {
            "atsScore": final_ats_score,
            "maxScore": 100,
            "scoreBreakdown": {
                "skillsMatch": int(round((skills_score / 25.0) * 100)),
                "experienceMatch": int(round((experience_combined_score / 35.0) * 100)),
                "projectMatch": project_match_percentage,
                "linkIntegrity": int(round((links_score / 5.0) * 100)),
                "educationScore": int(round((education_score / 5.0) * 100)),
                "totalMonths": total_months,                 
                "isFresher": is_fresher
            },
            "deterministicMetrics": {
                "linksModule": link_report,
                "skillsModule": skills_report,
                "educationModule": education_report,
                "experienceModule": experience_report,
                "projectModule": project_report_enriched
            },
            "hygieneCheck": hygiene_snapshot
        }

    def _enrich_project_nodes(
        self,
        project_report: Dict[str, Any],
        link_report: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Lookup-based project node enrichment using validated link metadata.
        """
        raw_eval = project_report.get("projectEvaluation", [])
        if not raw_eval or not isinstance(raw_eval, list):
            return []

        validated_links_map = link_report.get("raw_validator_links", {})

        enriched_list = []
        for proj in raw_eval:
            p_url = str(proj.get("projectUrl", "")).strip()

            link_info = validated_links_map.get(p_url, {})
            cat = link_info.get("category", "")
            is_valid = link_info.get("valid", False)

            repo_status = {
                "present": cat == "repository",
                "working": cat == "repository" and is_valid
            }

            deployment_status = {
                "present": cat == "deployment",
                "working": cat == "deployment" and is_valid
            }

            enriched_proj = {
                **proj,
                "repository": repo_status,
                "deployment": deployment_status
            }
            enriched_list.append(enriched_proj)

        return enriched_list

    def _generate_hygiene_snapshot(
        self,
        link_report: Dict[str, Any],
        education_report: Dict[str, Any],
        raw_resume_text: str = ""
    ) -> Dict[str, bool]:
        categories = link_report.get("categoryBreakdown", {})
        edu_analysis = education_report.get("educationAnalysis", {})

        li_data = categories.get("linkedin", {})
        gh_data = categories.get("github", {})
        port_data = categories.get("portfolio", {})

        text_lower = raw_resume_text.lower()
        has_email = "@" in text_lower and "." in text_lower
        has_phone = len([c for c in text_lower if c.isdigit()]) >= 10

        return {
            "hasLinkedIn": li_data.get("present", False),
            "linkedInWorking": li_data.get("working", False),
            "hasGithub": gh_data.get("present", False),
            "githubWorking": gh_data.get("working", False),
            "hasPortfolio": port_data.get("present", False),
            "portfolioWorking": port_data.get("working", False),
            "hasInstitution": edu_analysis.get("institution", {}).get("present", False),
            "hasDuration": edu_analysis.get("duration", {}).get("present", False),
            "hasScore": edu_analysis.get("grade", {}).get("present", False),
            "hasPhone": has_phone,
            "hasEmail": has_email
        }