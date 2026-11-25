import re
from typing import Dict, Any, List
from urllib.parse import urlparse


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

    def __init__(self) -> None:
        # Expanded modern framework, library, cloud, and AI catalog
        self.framework_keywords = {
            # Web & App Frameworks
            "react",
            "angular",
            "vue",
            "nextjs",
            "next.js",
            "express",
            "django",
            "flask",
            "fastapi",
            "spring",
            "springboot",
            "tailwind",
            "bootstrap",
            "svelte",
            "nuxt",
            # Languages & Runtimes
            "typescript",
            "javascript",
            "python",
            "java",
            "golang",
            "go",
            "c++",
            # Databases & ORMs
            "mongodb",
            "postgresql",
            "postgres",
            "mysql",
            "sqlite",
            "redis",
            "prisma",
            "supabase",
            "firebase",
            "dynamodb",
            # DevOps & Cloud Infrastructure
            "docker",
            "kubernetes",
            "k8s",
            "aws",
            "azure",
            "gcp",
            "kafka",
            "rabbitmq",
            "graphql",
            "websockets",
            "ci/cd",
            # AI / ML & Data Engineering
            "openai",
            "langchain",
            "tensorflow",
            "pytorch",
            "pandas",
            "numpy",
            "spark",
        }

        self.metrics_pattern = re.compile(
            r"\b(?:\d+(?:\.\d+)?\s*%\s*|\d+\s*\+\s*|\d+\s*k\b|\d+\s*users\b|\d+\s*ms\b|\d+\s*x\b)",
            re.IGNORECASE,
        )

    def _classify_project_evidence(
        self,
        project: Dict[str, Any],
        project_url: str = "",
        *,
        is_tech_or_data: bool = True,
    ) -> Dict[str, Any]:
        """
        Classifies project implementation evidence across 7 exhaustive edge cases:
        1. VERIFIED_LIVE: Working public repository or working live deployment (HTTP 200).
        2. DEPLOYMENT_SLEEPING: Live deployment server timed out or returned 502/503/504 (free-tier spin-down).
        3. PRIVATE_REPO: GitHub repo specified & user exists, but repo returns 404/403 (private visibility).
        4. BROKEN_404: Link provided but returns 404/dead and user not verified.
        5. GENERIC_ROOT_URL: Points to platform root (e.g. github.com, linkedin.com) without project path.
        6. INVALID_FORMAT: Malformed or unparseable URL string.
        7. NO_LINK_PROVIDED: No repository or deployment link provided.
        """
        repo = project.get("repository") or {}
        deployment = project.get("deployment") or {}

        submitted_url = str(
            project_url
            or (repo.get("url") if isinstance(repo, dict) else "")
            or (deployment.get("url") if isinstance(deployment, dict) else "")
            or ""
        ).strip()

        # 1. VERIFIED_LIVE
        repo_working = bool(isinstance(repo, dict) and repo.get("working"))
        dep_working = bool(isinstance(deployment, dict) and deployment.get("working"))
        if repo_working or dep_working:
            return {
                "status": "VERIFIED_LIVE",
                "badgeType": "success",
                "submittedUrl": submitted_url,
                "statusCode": 200,
                "platform": (
                    repo.get("platform")
                    if repo_working
                    else deployment.get("platform", "deployment")
                ),
                "isWorking": True,
                "evidenceScore": 7.0,
                "title": "Verified Public Code / Demo",
                "reason": "Public repository or live deployment link is verified and active.",
                "actionableFix": "No action required. Your project implementation is publicly verified.",
                "scoreImpact": "Full evidence points awarded (7.0/7.0).",
            }

        # 2. DEPLOYMENT_SLEEPING
        dep_sleeping = bool(
            isinstance(deployment, dict)
            and (
                deployment.get("is_sleeping_or_timeout")
                or deployment.get("status_code") in (502, 503, 504)
            )
        )
        if dep_sleeping:
            code = deployment.get("status_code")
            code_label = f"HTTP {code}" if code else "Request Timeout"
            return {
                "status": "DEPLOYMENT_SLEEPING",
                "badgeType": "warning",
                "submittedUrl": submitted_url,
                "statusCode": code,
                "platform": deployment.get("platform", "deployment"),
                "isWorking": False,
                "evidenceScore": 4.0,
                "title": "Live Demo Inactive or Sleeping",
                "reason": f"The deployment hosting server timed out or returned a temporary server error ({code_label}).",
                "actionableFix": "Server timed out or is inactive (free-tier spin-down). Ask candidate to spin up or wake up the live demo for interview review.",
                "scoreImpact": "Partial credit awarded (4.0/7.0). Site is unverified until active or demonstrated live in interview.",
            }

        # 3. PRIVATE_REPO
        repo_private = bool(
            isinstance(repo, dict)
            and (
                repo.get("is_private_or_missing_repo")
                or (repo.get("present") and repo.get("user_exists") and not repo.get("working"))
            )
        )
        if repo_private:
            return {
                "status": "PRIVATE_REPO",
                "badgeType": "warning",
                "submittedUrl": submitted_url,
                "statusCode": repo.get("status_code", 404),
                "platform": repo.get("platform", "github"),
                "isWorking": False,
                "evidenceScore": 2.5,
                "title": "GitHub Repository is Private",
                "reason": "GitHub account verified, but this repository cannot be viewed publicly (returns 404). Evaluators cannot inspect the code.",
                "actionableFix": "Repository exists but is set to private. Ask candidate to Make public or present their codebase during the technical interview.",
                "scoreImpact": "Partial credit awarded (2.5/7.0). Repository code remains unverified until reviewed in interview.",
            }

        # URL provided but not working
        if submitted_url:
            clean_lower = submitted_url.lower()
            # 5. GENERIC_ROOT_URL
            try:
                parsed = urlparse(
                    clean_lower if clean_lower.startswith(("http://", "https://")) else f"https://{clean_lower}"
                )
                path = (parsed.path or "").strip("/")
                if parsed.netloc in ("github.com", "gitlab.com", "bitbucket.org", "linkedin.com", "vercel.app", "netlify.app") and not path:
                    return {
                        "status": "GENERIC_ROOT_URL",
                        "badgeType": "warning",
                        "submittedUrl": submitted_url,
                        "statusCode": 200,
                        "platform": "generic",
                        "isWorking": False,
                        "evidenceScore": 1.0,
                        "title": "Generic Platform Link",
                        "reason": f"The link ({submitted_url}) points to a platform root rather than the specific project repository or demo.",
                        "actionableFix": "Link points to a platform root rather than this specific project. Request the direct URL or repository during the interview.",
                        "scoreImpact": "Minimal evidence credit (1.0/7.0). Direct project links are required for verification.",
                    }
            except Exception:
                pass

            # 6. INVALID_FORMAT
            if not clean_lower.startswith(("http://", "https://", "github.com", "gitlab.com", "bitbucket.org")) and "." not in clean_lower:
                return {
                    "status": "INVALID_FORMAT",
                    "badgeType": "error",
                    "submittedUrl": submitted_url,
                    "statusCode": 400,
                    "platform": "unknown",
                    "isWorking": False,
                    "evidenceScore": 1.0,
                    "title": "Malformed URL",
                    "reason": f"'{submitted_url}' is not a valid web link format.",
                    "actionableFix": "Project link format is invalid. Request the correct URL directly from the candidate during screening.",
                    "scoreImpact": "Minimal credit (1.0/7.0). Correct URL format required for verification.",
                }

            # 4. BROKEN_404
            status_code = (
                (repo.get("status_code") if isinstance(repo, dict) else None)
                or (deployment.get("status_code") if isinstance(deployment, dict) else None)
                or 404
            )
            return {
                "status": "BROKEN_404",
                "badgeType": "error",
                "submittedUrl": submitted_url,
                "statusCode": status_code,
                "platform": (
                    repo.get("platform")
                    if isinstance(repo, dict) and repo.get("platform")
                    else deployment.get("platform", "web")
                    if isinstance(deployment, dict)
                    else "web"
                ),
                "isWorking": False,
                "evidenceScore": 1.5,
                "title": "Broken Link (404 Not Found)",
                "reason": f"The submitted project link ({submitted_url}) returned 404 Not Found or is unreachable.",
                "actionableFix": "Project link returned 404 or is unreachable. Confirm with candidate if the repository was renamed or moved.",
                "scoreImpact": "Low evidence score (1.5/7.0). Link is unverified.",
            }

        # 7. NO_LINK_PROVIDED
        evidence_score = 0.5 if is_tech_or_data else 2.5
        fix_msg = (
            "No public code or demo was detected in the résumé. Ask candidate to demonstrate implementation or Add a public GitHub repository link / live demo."
            if is_tech_or_data
            else "No work sample provided. Ask candidate for work samples or evidence during interview."
        )
        return {
            "status": "NO_LINK_PROVIDED",
            "badgeType": "neutral",
            "submittedUrl": None,
            "statusCode": None,
            "platform": None,
            "isWorking": False,
            "evidenceScore": evidence_score,
            "title": "No Public Code or Demo Provided" if is_tech_or_data else "No Work Sample Provided",
            "reason": (
                "This project was evaluated based only on résumé bullet points. No public repository or live demo was detected."
                if is_tech_or_data
                else "This project was evaluated based on description details alone."
            ),
            "actionableFix": fix_msg,
            "scoreImpact": (
                "Unverified project score is capped at 60% without public code or live demo verification."
                if is_tech_or_data
                else "Work sample is unverified."
            ),
        }

    def score_projects(
        self,
        parsed_projects: List[Dict[str, Any]],
        *,
        role_keywords: List[str] | None = None,
        domain_profile: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        if not parsed_projects or not isinstance(parsed_projects, list):
            return self._build_empty_response()

        if domain_profile is not None:
            return self._score_domain_aware(
                parsed_projects,
                role_keywords=role_keywords or [],
                domain_profile=domain_profile,
            )

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
            has_framework = any(
                fw in body_text or fw in title.lower() for fw in self.framework_keywords
            )
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
                k in body_text
                for k in [
                    "scale",
                    "optimize",
                    "streamline",
                    "users",
                    "automated",
                    "latency",
                ]
            )
            metrics_pts = 7.0 if has_metrics else 3.0

            # Pillar 5: Verified Repository & Deployment Health (Max 7.0 pts)
            link_verif = self._classify_project_evidence(
                project, project_url, is_tech_or_data=True
            )
            verification_pts = link_verif["evidenceScore"]

            raw_total = round(
                context_pts + fw_pts + relevance_pts + metrics_pts + verification_pts, 1
            )
            if link_verif["status"] != "VERIFIED_LIVE":
                project_total = min(raw_total, 21.0)
            else:
                project_total = min(raw_total, 35.0)
            cumulative_score += project_total

            # Generate targeted gaps
            gaps = []
            if link_verif["status"] != "VERIFIED_LIVE":
                gaps.append(link_verif["actionableFix"])
            if not has_metrics:
                gaps.append(
                    "Project description could be strengthened by including quantitative impact or performance metrics."
                )
            if tech_count < 2:
                gaps.append(
                    "Expand on specific libraries, frameworks, or cloud databases used."
                )

            scored_project_list.append(
                {
                    "projectName": title,
                    "score": project_total,
                    "maxScore": 35,
                    "rating": self._get_rating_label(project_total),
                    "projectUrl": link_verif.get("submittedUrl") or project_url,
                    "detectedGaps": gaps,
                    "linkVerification": link_verif,
                    "breakdown": {
                        "structureDepth": context_pts >= 5.5,
                        "frameworkDeclarations": has_framework,
                        "stackDensity": tech_count >= 2,
                        "impactMetrics": has_metrics,
                        "verifiedDeploymentOrRepo": link_verif["status"] == "VERIFIED_LIVE",
                    },
                }
            )

        total_projects = len(parsed_projects)
        overall_score = (
            round(cumulative_score / total_projects, 1) if total_projects > 0 else 0.0
        )

        return {
            "totalScore": overall_score,
            "maxScore": 35,
            "overallRating": self._get_rating_label(overall_score),
            "projectCount": total_projects,
            "projectEvaluation": scored_project_list,
        }

    def _score_domain_aware(
        self,
        parsed_projects: List[Dict[str, Any]],
        *,
        role_keywords: List[str],
        domain_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Score work samples consistently across technical and non-technical roles.

        A repository or framework is never intrinsically valuable here. Projects
        earn points for understandable scope, candidate ownership, role relevance,
        outcomes and supporting evidence appropriate to the detected domain.
        """
        normalized_keywords = {
            str(keyword).strip().casefold()
            for keyword in role_keywords
            if str(keyword).strip()
        }
        ownership_words = {
            "led",
            "owned",
            "created",
            "built",
            "designed",
            "developed",
            "analysed",
            "analyzed",
            "managed",
            "launched",
            "implemented",
            "coordinated",
            "researched",
            "facilitated",
            "delivered",
        }
        outcome_words = {
            "improved",
            "increased",
            "reduced",
            "saved",
            "grew",
            "resolved",
            "streamlined",
            "optimized",
            "optimised",
            "adopted",
            "achieved",
            "impact",
            "outcome",
            "result",
        }
        domain_id = str(domain_profile.get("id") or "").lower()
        domain_label = str(domain_profile.get("label") or "").lower()
        is_tech_or_data = (
            any(k in domain_id for k in ("tech", "engineer", "data", "software"))
            or any(k in domain_label for k in ("tech", "engineer", "data", "software"))
            or any(
                k in kw.lower()
                for kw in normalized_keywords
                for k in ("sql", "python", "java", "react", "data", "developer", "engineer", "backend", "frontend", "sde")
            )
        )
        evaluations: list[Dict[str, Any]] = []
        total = 0.0

        for project in parsed_projects:
            title = str(
                project.get("title", project.get("projectName", "Unnamed Project"))
            )
            description = str(project.get("description") or "")
            highlights = project.get("highlights") or []
            if not isinstance(highlights, list):
                highlights = [str(highlights)]
            body = f"{title} {description} {' '.join(map(str, highlights))}".casefold()
            project_url = str(project.get("projectUrl") or "").strip()

            detail_length = len(body.strip())
            if len(highlights) >= 4 or detail_length > 240:
                clarity = 7.0
            elif len(highlights) >= 2 or detail_length > 120:
                clarity = 5.5
            elif detail_length > 45:
                clarity = 4.0
            else:
                clarity = 2.0

            ownership_matches = sorted(
                word for word in ownership_words if re.search(rf"\b{word}\b", body)
            )
            ownership_hits = len(ownership_matches)
            ownership = (
                7.0 if ownership_hits >= 3 else 5.5 if ownership_hits >= 1 else 3.0
            )

            matched_keywords = sorted(
                keyword for keyword in normalized_keywords if keyword in body
            )
            keyword_hits = len(matched_keywords)
            if not normalized_keywords:
                relevance = 4.5
            elif keyword_hits >= min(3, len(normalized_keywords)):
                relevance = 7.0
            elif keyword_hits >= 1:
                relevance = 5.0
            else:
                relevance = 2.5

            metric_match = self.metrics_pattern.search(body)
            has_metric = bool(metric_match)
            outcome_matches = sorted(
                word for word in outcome_words if re.search(rf"\b{word}\b", body)
            )
            has_outcome = bool(outcome_matches)
            outcomes = 7.0 if has_metric else 5.5 if has_outcome else 3.0

            link_verif = self._classify_project_evidence(
                project, project_url, is_tech_or_data=is_tech_or_data
            )
            evidence = link_verif["evidenceScore"]

            raw_total = round(
                clarity + ownership + relevance + outcomes + evidence, 1
            )
            # Cap unverified technical projects at 60% (21.0 / 35.0)
            if is_tech_or_data and link_verif["status"] != "VERIFIED_LIVE":
                project_total = min(raw_total, 21.0)
            else:
                project_total = min(raw_total, 35.0)
            total += project_total

            gaps: list[str] = []
            if link_verif["status"] != "VERIFIED_LIVE":
                gaps.append(link_verif["actionableFix"])
            if clarity < 5.5:
                gaps.append(
                    "Clarify the problem, your contribution, and the work completed."
                )
            if ownership < 5.5:
                gaps.append(
                    "State which decisions and deliverables you personally owned."
                )
            if relevance < 5.0 and normalized_keywords:
                gaps.append(
                    "Explain how this work demonstrates the role's requested capabilities."
                )
            if outcomes < 5.5:
                gaps.append(
                    "Add the result, learning, decision, or measurable impact of the work."
                )

            factor_evidence = {
                "clarity": (
                    f"The résumé gives {len(highlights)} project highlights and "
                    f"{len(description.strip())} description characters."
                ),
                "ownership": (
                    "Personal contribution is signalled by: "
                    + ", ".join(ownership_matches[:5])
                    + ". Confirm what the candidate personally delivered."
                    if ownership_matches
                    else "No clear action verb describes what the candidate personally delivered."
                ),
                "roleRelevance": (
                    "Requested capabilities named in this project: "
                    + ", ".join(matched_keywords[:5])
                    + "."
                    if matched_keywords
                    else "No requested capability was named in this project description."
                    if normalized_keywords
                    else "No role capabilities were supplied for a project-level comparison."
                ),
                "outcomes": (
                    f"A measurable claim ({metric_match.group().strip()}) is stated; verify its context."
                    if metric_match
                    else "An outcome is suggested by: "
                    + ", ".join(outcome_matches[:4])
                    + ". Ask for the actual result."
                    if outcome_matches
                    else "No result, learning, decision, or measurable impact is stated."
                ),
                "supportingEvidence": (
                    f"{link_verif['title']}: {link_verif['reason']} {link_verif['scoreImpact']}"
                ),
            }

            evaluations.append(
                {
                    "projectName": title,
                    "score": project_total,
                    "maxScore": 35,
                    "rating": self._get_rating_label(project_total),
                    "projectUrl": link_verif.get("submittedUrl") or project_url,
                    "detectedGaps": gaps,
                    "linkVerification": link_verif,
                    "factorEvidence": factor_evidence,
                    "domain": domain_profile.get("label", "General business"),
                    "breakdown": {
                        "clarity": clarity,
                        "ownership": ownership,
                        "roleRelevance": relevance,
                        "outcomes": outcomes,
                        "supportingEvidence": evidence,
                    },
                }
            )

        overall = round(total / len(evaluations), 1) if evaluations else 0.0
        return {
            "totalScore": overall,
            "maxScore": 35,
            "overallRating": self._get_rating_label(overall),
            "projectCount": len(evaluations),
            "scoringModel": "domain_aware_v1",
            "domain": domain_profile.get("id", "general_business"),
            "projectEvaluation": evaluations,
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
            "projectEvaluation": [],
        }
