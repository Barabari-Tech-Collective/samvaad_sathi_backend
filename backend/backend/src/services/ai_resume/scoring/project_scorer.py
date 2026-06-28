import re
from typing import Dict, Any, List

class ProjectScorer:
    """
    Deterministic Project Scorer for the ATS Engine.
    Evaluates projects individually using a strict 40-point rubric:
    - Frameworks Used: +10 points
    - GitHub Link:     +10 points
    - Deployment Link: +10 points (Vercel, Netlify, Render, AWS, etc.)
    - Metrics/Impact:  +10 points
    """

    def __init__(self):
        self.framework_keywords = {
            "react", "angular", "vue", "nextjs", "next.js", "express", 
            "django", "flask", "fastapi", "spring", "springboot", 
            "tailwind", "bootstrap", "mongodb", "postgresql", "mysql"
        }
        self.metrics_pattern = re.compile(r'\b(?:\d+(?:\.\d+)?\s*%\s*|\d+\s*\+\s*|\d+\s*k\b|\d+\s*ms\b|\d+\s*x\b)', re.IGNORECASE)

    def score_projects(self, parsed_projects: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not parsed_projects or not isinstance(parsed_projects, list):
            return self._build_empty_response()

        scored_project_list = []
        cumulative_score = 0.0

        for project in parsed_projects:
            title = project.get("title", project.get("projectName", "Unnamed Project"))
            desc = project.get("description", "")
            highlights = project.get("highlights", [])
            direct_url = project.get("projectUrl", project.get("url", ""))

            # Combine structural text descriptions to scan for words
            body_text = f"{desc} {' '.join(highlights)}".lower()
            url_text = str(direct_url).lower()

            # 1. Framework Check (+10)
            has_framework = any(fw in body_text or fw in title.lower() for fw in self.framework_keywords)
            fw_points = 10 if has_framework else 0

            # # 2. GitHub Code Repository Check (+10)
            # has_github = "github.com" in url_text or "github.com" in body_text or "gitlab.com" in url_text
            # gh_points = 10 if has_github else 0

            # # 3. Live Cloud Deployment URL Check (+10)
            # # Checks if they have an active link that isn't just code repositories
            # has_deployment = any(host in url_text or host in body_text for host in ["vercel", "netlify", "render.com", "heroku", "aws", "github.io", "amplify", "pages.dev"])
            # if direct_url and "github.com" not in url_text and "gitlab.com" not in url_text:
            #     has_deployment = True
            # deploy_points = 10 if has_deployment else 0
            # 2. GitHub Code Repository Check (+10)
            has_github = "github.com" in url_text or "github.com" in body_text or "gitlab.com" in url_text or "github" in body_text
            # If they have anchor link text indicators next to the code stack, treat it as present
            if "link:" in body_text and any(tech in body_text for tech in ["react", "node", "express", "python"]):
                has_github = True
            gh_points = 10 if has_github else 0

            # 3. Live Cloud Deployment URL Check (+10)
            has_deployment = any(host in url_text or host in body_text for host in ["vercel", "netlify", "render.com", "heroku", "aws", "github.io"])
            if direct_url and "github.com" not in url_text:
                has_deployment = True
            # FIXED FALLBACK: If the text states a deployment link keyword indicator like "link: here", do not penalize it with a 0
            if "link:" in body_text or "url:" in body_text or "demo" in body_text:
                has_deployment = True
            deploy_points = 10 if has_deployment else 0

            # 4. Impact Metrics Check (+10)
            has_metrics = bool(self.metrics_pattern.search(body_text))
            metrics_points = 10 if has_metrics else 0

            project_total = fw_points + gh_points + deploy_points + metrics_points
            cumulative_score += project_total

            # Document granular omissions for targeted feedback loops
            gaps = []
            if not has_framework: gaps.append("Missing explicit framework declarations")
            if not has_github: gaps.append("Missing functional GitHub repository link")
            if not has_deployment: gaps.append("Missing live application deployment URL (Vercel/Netlify/Cloud)")
            if not has_metrics: gaps.append("Missing quantitative impact metrics (e.g. loading speeds, scale benchmarks)")

            scored_project_list.append({
                "projectName": title,
                "score": project_total,
                "maxScore": 40,
                "rating": self._get_rating_label(project_total),
                "projectUrl": direct_url if direct_url else "",
                "detectedGaps": gaps,
                "breakdown": {
                    "hasFramework": has_framework,
                    "hasGithub": has_github,
                    "hasDeployment": has_deployment,
                    "hasMetrics": has_metrics
                }
            })

        total_projects = len(parsed_projects)
        overall_score = int(round(cumulative_score / total_projects)) if total_projects > 0 else 0

        return {
            "totalScore": overall_score,
            "maxScore": 40,
            "overallRating": self._get_rating_label(overall_score),
            "projectCount": total_projects,
            "projectEvaluation": scored_project_list
        }

    def _get_rating_label(self, score: int) -> str:
        if score <= 10: return "Needs Improvement"
        elif score <= 20: return "Average"
        elif score <= 30: return "Good"
        else: return "Excellent"

    def _build_empty_response(self) -> Dict[str, Any]:
        return {"totalScore": 0, "maxScore": 40, "overallRating": "Needs Improvement", "projectCount": 0, "projectEvaluation": []}