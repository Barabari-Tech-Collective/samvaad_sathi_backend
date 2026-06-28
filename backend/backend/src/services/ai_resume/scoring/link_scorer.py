# this code give scoring based on working links present in the resume
import re
from typing import Dict, Any

class LinkScorer:
    """
    Deterministic Link Scorer for the ATS Engine.
    Consumes output from SmartLinkValidator, handles engineering vs designer tracks,
    calculates metrics out of 40, and formats the output for frontend rendering.
    """
    
    def __init__(self):
        # Target keywords to identify design-oriented tracks if not specified explicitly
        self.design_keywords = re.compile(r'(ui/ux|product designer|graphic|figma|illustrator|behance|dribbble)', re.IGNORECASE)

    def score_links(self, validator_output: Dict[str, Any], track: str = None, job_title: str = "") -> Dict[str, Any]:
        """
        Main interface to process and score links.
        
        :param validator_output: The exact output dictionary from SmartLinkValidator.validate_all_links_async()
        :param track: Explicit path setting ('engineering' or 'designer')
        :param job_title: Job description or title to fallback-infer track type
        """
        # 1. Flatten and categorize the complex output structure from your SmartLinkValidator
        normalized_links = self._normalize_validator_data(validator_output)
        
        # 2. Determine processing track route
        if not track:
            track = self._infer_track(job_title, normalized_links)
            
        # 3. Calculate scores based on track
        if track == 'designer':
            return self._score_designer_track(normalized_links)
        else:
            return self._score_engineering_track(normalized_links)

    def _normalize_validator_data(self, validator_output: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """
        Flattens the 'links' section of your validator into a key-value map by platform type.
        Ensures statuses map smoothly into ('valid' | 'broken' | 'missing').
        """
        # Default map structure
        flat_map = {
            "linkedin": {"status": "missing", "url": ""},
            "github": {"status": "missing", "url": ""},
            "behance": {"status": "missing", "url": ""},
            "dribbble": {"status": "missing", "url": ""},
            "portfolio": {"status": "missing", "url": ""}
        }
        
        raw_links = validator_output.get("links", {})
        
        for original_url, details in raw_links.items():
            url = details.get("normalized_url", "").lower()
            is_valid = details.get("valid", False)
            status_str = "valid" if is_valid else "broken"
            
            # Map based on domain matching
            if "linkedin.com" in url:
                flat_map["linkedin"] = {"status": status_str, "url": original_url}
            elif "github.com" in url:
                flat_map["github"] = {"status": status_str, "url": original_url}
            elif "behance.net" in url:
                flat_map["behance"] = {"status": status_str, "url": original_url}
            elif "dribbble.com" in url:
                flat_map["dribbble"] = {"status": status_str, "url": original_url}
            else:
                # Treat other active URLs (like personal web domains or custom URLs) as portfolio targets
                if flat_map["portfolio"]["status"] != "valid": 
                    flat_map["portfolio"] = {"status": status_str, "url": original_url}
                    
        return flat_map

    def _infer_track(self, job_title: str, normalized_links: Dict[str, Any]) -> str:
        """Helper to auto-switch tracks based on job title or parsed platforms."""
        if self.design_keywords.search(job_title):
            return 'designer'
        if normalized_links["behance"]["status"] == "valid" or normalized_links["dribbble"]["status"] == "valid":
            return 'designer'
        return 'engineering'

    def _get_link_metrics(self, platform_data: Dict[str, Any], max_points: int) -> tuple:
        """Translates structural validation statuses into concrete point values and string indicators."""
        status = platform_data.get("status", "missing")
        
        if status == "valid":
            return True, True, max_points, "Working"
        elif status == "broken":
            return True, False, 0, "Broken link detected"
        else:
            return False, False, 0, "Missing"

    def _score_engineering_track(self, links: Dict[str, Any]) -> Dict[str, Any]:
        """Calculates exact metrics for Engineering Track (Max 40)."""
        li_pres, li_work, li_score, li_feed = self._get_link_metrics(links["linkedin"], 10)
        gh_pres, gh_work, gh_score, gh_feed = self._get_link_metrics(links["github"], 10)
        proj_pres, proj_work, proj_score, proj_feed = self._get_link_metrics(links["portfolio"], 20)
        
        return {
            "track": "engineering",
            "totalScore": li_score + gh_score + proj_score,
            "maxScore": 40,
            "linkAnalysis": {
                "linkedIn": {"present": li_pres, "working": li_work, "score": li_score, "maxScore": 10, "feedback": li_feed},
                "github": {"present": gh_pres, "working": gh_work, "score": gh_score, "maxScore": 10, "feedback": gh_feed},
                "projects": {"present": proj_pres, "working": proj_work, "score": proj_score, "maxScore": 20, "feedback": proj_feed}
            }
        }

    def _score_designer_track(self, links: Dict[str, Any]) -> Dict[str, Any]:
        """Calculates exact metrics for Designer Track (Max 40)."""
        li_pres, li_work, li_score, li_feed = self._get_link_metrics(links["linkedin"], 10)
        
        # Design track platform logic evaluation (Behance or Dribbble can fulfill this category)
        bh_status = links["behance"]["status"]
        db_status = links["dribbble"]["status"]
        
        if bh_status == "valid" or db_status == "valid":
            design_pres, design_work, design_score, design_feed = True, True, 20, "Working"
        elif bh_status == "broken" or db_status == "broken":
            design_pres, design_work, design_score, design_feed = True, False, 0, "Broken link detected on design profile"
        else:
            design_pres, design_work, design_score, design_feed = False, False, 0, "Missing Behance or Dribbble profile"
            
        live_pres, live_work, live_score, live_feed = self._get_link_metrics(links["portfolio"], 10)
        
        return {
            "track": "designer",
            "totalScore": li_score + design_score + live_score,
            "maxScore": 40,
            "linkAnalysis": {
                "linkedIn": {"present": li_pres, "working": li_work, "score": li_score, "maxScore": 10, "feedback": li_feed},
                "designPlatform": {"present": design_pres, "working": design_work, "score": design_score, "maxScore": 20, "feedback": design_feed},
                "livePortfolio": {"present": live_pres, "working": live_work, "score": live_score, "maxScore": 10, "feedback": live_feed}
            }
        }