from typing import Dict, List, Any, Optional
import re
from urllib.parse import urlparse


class ProjectLinkMapper:
    """
    Multi-Signal Weighted Project Link Mapper.
    Associates validated URLs to candidate projects using multi-factor scoring:
    1. Spatial Proximity / BBox Proximity (40 pts)
    2. Tech Stack Context Co-occurrence (25 pts)
    3. Direct Text Footprint Inlining (20 pts)
    4. Title Similarity / Keyword Intersections (15 pts)
    """

    def __init__(self):
        self.stop_words = {
            "project", "app", "application", "tool", "system", "website",
            "dashboard", "management", "built", "using", "with", "and", "the"
        }

    def _extract_tech_tokens(self, text: str) -> set:
        """Extracts common technology keywords for stack overlap matching."""
        tech_catalog = {
            "react", "node", "nodejs", "express", "mongodb", "postgres", "postgresql",
            "mysql", "python", "django", "flask", "fastapi", "java", "spring",
            "typescript", "javascript", "docker", "aws", "redis", "nextjs", "vue"
        }
        text_lower = text.lower()
        return {t for t in tech_catalog if t in text_lower}

    def _calculate_title_similarity(self, title: str, url: str) -> float:
        """Calculates token overlap between project title and repository path."""
        title_tokens = {
            t.lower() for t in re.findall(r'\w+', title)
            if len(t) > 2 and t.lower() not in self.stop_words
        }
        if not title_tokens:
            return 0.0

        url_path = urlparse(url).path.lower()
        matched = sum(1 for token in title_tokens if token in url_path)
        return (matched / len(title_tokens)) * 15.0

    def map_links_to_projects(
        self,
        projects: List[Dict[str, Any]],
        validated_links: Dict[str, Any],
        document_map: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Maps validated links uniquely to projects using weighted confidence scores.
        Ensures each repository or deployment link is claimed by at most one project.
        """
        if not projects or not isinstance(projects, list):
            return []

        links_data = validated_links.get("links", {})
        
        # Filter for candidate repositories and deployments only
        candidate_urls = [
            raw_url for raw_url, details in links_data.items()
            if details.get("category") in ["repository", "deployment"]
        ]

        assigned_urls = set()

        for proj in projects:
            p_title = str(proj.get("title", proj.get("projectName", ""))).strip()
            p_desc = f"{proj.get('description', '')} {' '.join(proj.get('highlights', []))}"
            p_tech = self._extract_tech_tokens(f"{p_title} {p_desc}")

            # Clear out existing non-URL artifacts
            current_url = str(proj.get("projectUrl", "")).strip()
            if current_url and not current_url.lower().startswith("http"):
                proj["projectUrl"] = ""

            # If project already has a valid URL assigned, mark it used
            if proj.get("projectUrl") and proj.get("projectUrl") in candidate_urls:
                assigned_urls.add(proj["projectUrl"])
                continue

            best_repo = None
            highest_repo_confidence = 0.0
            best_deployment = None
            highest_deployment_confidence = 0.0

            for raw_url in candidate_urls:
                if raw_url in assigned_urls:
                    continue

                confidence_score = 0.0
                clean_url = raw_url.lower().strip()

                # Signal 1: Direct Inlined Text Footprint Match (+20 pts)
                if clean_url in p_desc.lower() or clean_url.replace("https://", "").replace("http://", "") in p_desc.lower():
                    confidence_score += 20.0

                # Signal 2: Tech Stack Co-occurrence Overlap (+25 pts)
                url_meta = links_data.get(raw_url, {})
                url_platform = url_meta.get("platform", "")
                if url_platform in p_tech or any(tech in clean_url for tech in p_tech):
                    confidence_score += 25.0

                # Signal 3: Title Keyword Similarity (+15 pts)
                title_score = self._calculate_title_similarity(p_title, raw_url)
                confidence_score += title_score

                # Signal 4: Spatial Document Map BBox Distance (+40 pts if spatial context available)
                if document_map:
                    for entry in document_map:
                        if raw_url in entry.get("text", "") and p_title.lower() in entry.get("text", "").lower():
                            confidence_score += 40.0
                            break

                if confidence_score >= 15.0:
                    url_cat = url_meta.get("category")
                    if url_cat == "repository" and confidence_score > highest_repo_confidence:
                        highest_repo_confidence = confidence_score
                        best_repo = raw_url
                    elif url_cat == "deployment" and confidence_score > highest_deployment_confidence:
                        highest_deployment_confidence = confidence_score
                        best_deployment = raw_url

            if best_repo:
                assigned_urls.add(best_repo)
                is_valid = links_data.get(best_repo, {}).get("valid", False)
                proj["repository"] = {"present": True, "working": is_valid}
                if not proj.get("projectUrl"):
                    proj["projectUrl"] = best_repo
                print(f"Project Mapper Bound Repo: '{p_title}' -> '{best_repo}' (Confidence Score: {highest_repo_confidence})")
            
            if best_deployment:
                assigned_urls.add(best_deployment)
                is_valid = links_data.get(best_deployment, {}).get("valid", False)
                proj["deployment"] = {"present": True, "working": is_valid}
                if not proj.get("projectUrl") or (proj.get("projectUrl") == best_repo and "github" in best_repo.lower()):
                    # Prefer deployment link as the primary project URL if the current one is just a repo
                    proj["projectUrl"] = best_deployment
                print(f"Project Mapper Bound Deployment: '{p_title}' -> '{best_deployment}' (Confidence Score: {highest_deployment_confidence})")

        return projects