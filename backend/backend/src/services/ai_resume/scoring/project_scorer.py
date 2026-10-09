import re
from typing import Dict, Any, List

class ProjectScorer:
    """
    Deterministic Project Scorer for the ATS Engine.
    Evaluates projects across 5 pillars without performing text link extraction:
    1. Presentation Structure & Highlights Depth (Max 7.0 pts)
    2. Framework & Tool Declarations (Max 7.0 pts)
    3. Tech Stack Density & Relevance (Max 7.0 pts)
    4. Quantitative Performance Metrics (Max 7.0 pts)
    5. Verified Repository & Live Deployment Status (Max 7.0 pts)
    ----------------------------------------------------------
    Total Max Score Per Project: 35.0 Points
    """

    def __init__(self):
        # Expanded modern framework, library, cloud, and AI catalog
        self.framework_keywords = {
            # Web & App Frameworks
            "react", "angular", "vue", "nextjs", "next.js", "express", 
            "django", "flask", "fastapi", "spring", "springboot", 
            "tailwind", "bootstrap", "svelte", "nuxt",
            
            # Languages & Runtimes
            "typescript", "javascript", "python", "java", "golang", "go", "c++",
            
            # Databases & ORMs
            "mongodb", "postgresql", "postgres", "mysql", "sqlite", "redis",
            "prisma", "supabase", "firebase", "dynamodb",
            
            # DevOps & Cloud Infrastructure
            "docker", "kubernetes", "k8s", "aws", "azure", "gcp",
            "kafka", "rabbitmq", "graphql", "websockets", "ci/cd",
            
            # AI / ML & Data Engineering
            "openai", "langchain", "tensorflow", "pytorch", "pandas",
            "numpy", "spark"
        }

        self.metrics_pattern = re.compile(
            r'\b(?:\d+(?:\.\d+)?\s*%\s*|\d+\s*\+\s*|\d+\s*k\b|\d+\s*users\b|\d+\s*ms\b|\d+\s*x\b)', 
            re.IGNORECASE
        )

    def score_projects(self, parsed_projects: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not parsed_projects or not isinstance(parsed_projects, list):
            return self._build_empty_response()

        scored_project_list = []
        cumulative_score = 0.0

        for project in parsed_projects:
            title = project.get("title", project.get("projectName", "Unnamed Project"))
            desc = project.get("description", "")
            highlights = project.get("highlights", [])
            project_url = project.get("projectUrl", "")

            body_text = f"{desc} {' '.join(highlights)}".lower()

            # Pillar 1: Presentation & Highlights Structure (Max 7.0 pts)
            # Scores project depth using bullet highlight counts and text length
            highlight_count = len(highlights) if isinstance(highlights, list) else 0
            if highlight_count >= 5 or len(body_text.strip()) > 250:
                context_pts = 7.0
            elif highlight_count >= 3 or len(body_text.strip()) > 120:
                context_pts = 5.5
            elif highlight_count >= 1 or len(body_text.strip()) > 40:
                context_pts = 4.0
            else:
                context_pts = 2.0

            # Pillar 2: Technical Frameworks & Tools Declarations (Max 7.0 pts)
            has_framework = any(fw in body_text or fw in title.lower() for fw in self.framework_keywords)
            fw_pts = 7.0 if has_framework else 3.0

            # Pillar 3: Tech Stack Density & Broad Relevance (Max 7.0 pts)
            tech_count = sum(1 for fw in self.framework_keywords if fw in body_text)
            if tech_count >= 4:
                relevance_pts = 7.0
            elif tech_count >= 2:
                relevance_pts = 5.5
            else:
                relevance_pts = 3.0

            # Pillar 4: Quantitative Impact Benchmarks (Max 7.0 pts)
            has_metrics = bool(self.metrics_pattern.search(body_text)) or any(
                k in body_text for k in ["scale", "optimize", "streamline", "users", "automated", "latency"]
            )
            metrics_pts = 7.0 if has_metrics else 3.0

            # Pillar 5: Verified Repository & Deployment Health (Max 7.0 pts)
            repo_status = project.get("repository", {})
            deployment_status = project.get("deployment", {})

            verification_pts = 0.0
            if repo_status.get("working"):
                verification_pts += 4.0
            elif repo_status.get("present") or "github.com" in str(project_url).lower():
                verification_pts += 2.0

            if deployment_status.get("working"):
                verification_pts += 3.0
            elif deployment_status.get("present"):
                verification_pts += 1.5

            if verification_pts == 0.0 and project_url:
                verification_pts = 2.0  # Fallback for unclassified working web URLs

            project_total = round(context_pts + fw_pts + relevance_pts + metrics_pts + verification_pts, 1)
            project_total = min(project_total, 35.0)
            cumulative_score += project_total

            # Generate targeted gaps
            gaps = []
            
            if repo_status.get("working") and deployment_status.get("working"):
                gaps.append("Excellent! Both a working repository and a live deployment link were successfully verified.")
            else:
                # Repo status checks
                if repo_status.get("present") and not repo_status.get("working"):
                    gaps.append("A repository link was detected, but it appears to be broken or private. Ensure the link is public and functioning.")
                elif not repo_status.get("present"):
                    gaps.append("A working repository link (e.g., GitHub) is missing. Adding the source code repository would maximize your score.")
                    
                # Deployment status checks
                if deployment_status.get("present") and not deployment_status.get("working"):
                    gaps.append("A live deployment link was detected, but it appears to be broken. Ensure the application is actively hosted.")
                elif not deployment_status.get("present"):
                    gaps.append("A live deployment website link is missing. Adding a live deployment link would maximize your score.")
            if not has_metrics:
                gaps.append("Project description could be strengthened by including quantitative impact or performance metrics.")
            if tech_count < 2:
                gaps.append("Expand on specific libraries, frameworks, or cloud databases used.")

            scored_project_list.append({
                "projectName": title,
                "score": project_total,
                "maxScore": 35,
                "rating": self._get_rating_label(project_total),
                "projectUrl": project_url,
                "detectedGaps": gaps,
                "breakdown": {
                    "structureDepth": context_pts >= 5.5,
                    "frameworkDeclarations": has_framework,
                    "stackDensity": tech_count >= 2,
                    "impactMetrics": has_metrics,
                    "verifiedDeploymentOrRepo": verification_pts >= 4.0
                }
            })

        total_projects = len(parsed_projects)
        overall_score = round(cumulative_score / total_projects, 1) if total_projects > 0 else 0.0

        return {
            "totalScore": overall_score,
            "maxScore": 35,
            "overallRating": self._get_rating_label(overall_score),
            "projectCount": total_projects,
            "projectEvaluation": scored_project_list
        }

    def _get_rating_label(self, score: float) -> str:
        if score <= 15:
            return "Needs Improvement"
        elif score <= 26:
            return "Average"
        elif score <= 31:
            return "Good"
        else:
            return "Excellent"

    def _build_empty_response(self) -> Dict[str, Any]:
        return {
            "totalScore": 0.0,
            "maxScore": 35,
            "overallRating": "Needs Improvement",
            "projectCount": 0,
            "projectEvaluation": []
        }