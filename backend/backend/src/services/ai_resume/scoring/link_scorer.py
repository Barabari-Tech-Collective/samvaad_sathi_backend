from typing import Dict, Any, List, Optional


class LinkScorer:
    """
    Production-Grade Link Scorer.
    Evaluates proof-of-work across profile identity, active deployments,
    mapped project repositories, packages, and certifications.
    """

    def __init__(self):
        pass

    def _calculate_diminishing_score(self, count: int, scale_map: Dict[int, float], max_val: float) -> float:
        """Helper to apply non-linear diminishing returns based on count tiers."""
        if count <= 0:
            return 0.0
        return scale_map.get(count, max_val)

    def score_links(
        self,
        validator_output: Dict[str, Any],
        mapped_projects: Optional[List[Dict[str, Any]]] = None,
        track: str = "engineering",
        job_title: str = ""
    ) -> Dict[str, Any]:
        links_map = validator_output.get("links", {})

        categories = {
            "linkedin": [],
            "github_profile": [],
            "portfolio": [],
            "deployments": [],
            "repositories": [],
            "packages": [],
            "certifications": [],
            "coding_profiles": []
        }

        for url, details in links_map.items():
            cat = details.get("category", "")
            platform = details.get("platform", "")
            is_valid = details.get("valid", False)

            link_payload = {
                "url": url,
                "valid": is_valid,
                "platform": platform,
                "status_code": details.get("status_code", 0)
            }

            if platform == "linkedin":
                categories["linkedin"].append(link_payload)
            elif platform == "github" and details.get("sub_type") != "repository":
                categories["github_profile"].append(link_payload)
            elif cat == "portfolio":
                categories["portfolio"].append(link_payload)
            elif cat == "deployment":
                categories["deployments"].append(link_payload)
            elif cat == "repository":
                categories["repositories"].append(link_payload)
            elif cat == "package":
                categories["packages"].append(link_payload)
            elif cat == "certification":
                categories["certifications"].append(link_payload)
            elif cat == "coding_profile":
                categories["coding_profiles"].append(link_payload)

        # 1. Score Pillar 1: Identity & Profiles (Max 2.0 Pts)
        li_valid = any(l["valid"] for l in categories["linkedin"])
        gh_valid = any(l["valid"] for l in categories["github_profile"])
        port_valid = any(l["valid"] for l in categories["portfolio"])

        profile_score = 0.0
        if li_valid: profile_score += 0.8
        if gh_valid: profile_score += 0.8
        if port_valid: profile_score += 0.4
        profile_score = min(profile_score, 2.0)

        # 2. Score Pillar 2: Proof-of-Work with Diminishing Returns (Max 2.0 Pts)
        valid_deploy_count = len([l for l in categories["deployments"] if l["valid"]])
        valid_repo_count = len([l for l in categories["repositories"] if l["valid"]])

        mapped_count = 0
        if mapped_projects:
            for p in mapped_projects:
                p_url = p.get("projectUrl", "")
                if p_url and links_map.get(p_url, {}).get("valid"):
                    mapped_count += 1

        # Tiered scaling maps: 1 -> 0.5, 2 -> 0.8, 3+ -> 1.0
        deploy_points = self._calculate_diminishing_score(valid_deploy_count, {1: 0.5, 2: 0.8}, 1.0)
        repo_points = self._calculate_diminishing_score(valid_repo_count, {1: 0.3, 2: 0.5}, 0.6)
        mapped_points = self._calculate_diminishing_score(mapped_count, {1: 0.2, 2: 0.3}, 0.4)

        proof_score = min(round(deploy_points + repo_points + mapped_points, 2), 2.0)

        # 3. Score Pillar 3: Technical Extras (Max 1.0 Pt)
        valid_packages = [l for l in categories["packages"] if l["valid"]]
        valid_certs = [l for l in categories["certifications"] if l["valid"]]
        valid_coding = [l for l in categories["coding_profiles"] if l["valid"]]

        extras_score = 0.0
        if valid_packages: extras_score += 0.5
        if valid_certs: extras_score += 0.5
        if valid_coding: extras_score += 0.5
        extras_score = min(extras_score, 1.0)

        total_score = round(profile_score + proof_score + extras_score, 1)

        has_local_links = any(url.lower().startswith("file://") or "c:/" in url.lower() or "users/" in url.lower() for url in links_map.keys())

        return {
            "totalScore": total_score,
            "hasLocalLinks": has_local_links,
            "maxScore": 5.0,
            "track": track,
            "categoryBreakdown": {
                "linkedin": {"present": len(categories["linkedin"]) > 0, "working": li_valid, "count": len(categories["linkedin"])},
                "github": {"present": len(categories["github_profile"]) > 0 or valid_repo_count > 0, "working": gh_valid or valid_repo_count > 0, "repositoryCount": valid_repo_count},
                "portfolio": {"present": len(categories["portfolio"]) > 0, "working": port_valid},
                "deployments": {"present": len(categories["deployments"]) > 0, "working": valid_deploy_count > 0, "activeCount": valid_deploy_count},
                "packages": {"present": len(categories["packages"]) > 0, "working": len(valid_packages) > 0, "count": len(valid_packages)},
                "certifications": {"present": len(categories["certifications"]) > 0, "working": len(valid_certs) > 0, "count": len(valid_certs)},
                "coding_profiles": {"present": len(categories["coding_profiles"]) > 0, "working": len(valid_coding) > 0, "count": len(valid_coding)}
            },
            "linkAnalysis": {
                "linkedIn": {"present": len(categories["linkedin"]) > 0, "working": li_valid, "score": 0.8 if li_valid else 0.0},
                "github": {"present": len(categories["github_profile"]) > 0, "working": gh_valid, "score": 0.8 if gh_valid else 0.0},
                "projects": {"present": port_valid or valid_deploy_count > 0, "working": port_valid or valid_deploy_count > 0, "score": proof_score}
            }
        }