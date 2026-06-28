import re
import httpx
from urllib.parse import urlparse
from typing import Dict, List, Tuple

class SmartLinkValidator:
    """
    Asynchronously validates all types of links extracted from raw resume text
    without blocking the FastAPI event loop.
    """
    def __init__(self, timeout: int = 4):
        self.timeout = timeout
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

    def extract_all_link_formats(self, text: str) -> List[Dict]:
        links_found = []
        
        # Pattern 1: Standard HTTP/HTTPS URLs
        pattern_http = r'https?://[^\s\)]+|www\.[^\s\)]+'
        for match in re.finditer(pattern_http, text):
            url = match.group().rstrip('.,;:()')
            links_found.append({
                "original": url,
                "normalized": self._normalize_url(url),
                "type": "standard_url"
            })
        
        # Pattern 2: Domain-only links
        pattern_domain = r'(?:github\.com|gitlab\.com|bitbucket\.org|linkedin\.com|behance\.net|dribbble\.com|figma\.com|twitter\.com|instagram\.com|youtube\.com|medium\.com|[a-zA-Z0-9-]+\.[a-zA-Z]{2,})/[^\s\)]+'
        for match in re.finditer(pattern_domain, text):
            url = match.group().rstrip('.,;:()')
            if not any(link["original"] == url for link in links_found):
                links_found.append({
                    "original": url,
                    "normalized": self._normalize_url(url),
                    "type": "text_link"
                })
        
        # Pattern 3: Text-based link indicators
        pattern_text_label = r'(?:GitHub|GitLab|Bitbucket|LinkedIn|Portfolio|Website|Blog|Behance|Dribbble|Medium)[\s:]*([^\s\)]+(?:github\.com|gitlab\.com|linkedin\.com|behance\.net|dribbble\.com|medium\.com|[a-zA-Z0-9-]+\.[a-zA-Z]{2,})/[^\s\)]+)'
        for match in re.finditer(pattern_text_label, text, re.IGNORECASE):
            url = match.group(1).rstrip('.,;:()')
            if not any(link["original"] == url for link in links_found):
                links_found.append({
                    "original": url,
                    "normalized": self._normalize_url(url),
                    "type": "labeled_link"
                })
        
        # Pattern 4: Plain domain URLs without path
        pattern_domain_only = r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b'
        for match in re.finditer(pattern_domain_only, text):
            url = match.group().rstrip('.,;:()')
            if not any(word in url.lower() for word in ['the', 'and', 'or', 'for', 'with', 'from']):
                if not any(link["original"] == url for link in links_found):
                    links_found.append({
                        "original": url,
                        "normalized": self._normalize_url(url),
                        "type": "domain_only"
                    })
        return links_found

    def _normalize_url(self, url: str) -> str:
        url = url.strip()
        if url.startswith(('http://', 'https://', 'ftp://')):
            return url
        if url.startswith('www.') or any(domain in url for domain in ['github.com', 'gitlab.com', 'linkedin.com', 'behance.net', 'dribbble.com', 'figma.com']):
            return f"https://{url}"
        return f"https://{url}"

    def is_valid_url_format(self, url: str) -> bool:
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except:
            return False

    async def check_link_active_async(self, client: httpx.AsyncClient, url: str) -> Tuple[bool, int, str]:
        """Non-blocking asynchronous head/get checks."""
        try:
            response = await client.head(url, timeout=self.timeout, follow_redirects=True, headers=self.headers)
            if response.status_code < 400:
                return True, response.status_code, ""
            
            # Fallback to GET if HEAD method is disallowed by platform (e.g., LinkedIn/Behance walls)
            response = await client.get(url, timeout=self.timeout, follow_redirects=True, headers=self.headers)
            return response.status_code < 400, response.status_code, ""
        except Exception as e:
            return False, 0, str(e)

    async def validate_github_profile_async(self, client: httpx.AsyncClient, url: str) -> Dict:
        """Asynchronous GitHub API checker."""
        try:
            match = re.search(r'github\.com/([^/]+)/?([^/]*)', url)
            if not match:
                return {"valid": False, "reason": "Invalid GitHub Format"}
            
            username, repo = match.group(1), match.group(2)
            api_url = f"https://api.github.com/repos/{username}/{repo}" if repo else f"https://api.github.com/users/{username}"
            
            response = await client.get(api_url, timeout=self.timeout)
            if response.status_code == 200:
                data = response.json()
                return {
                    "valid": True,
                    "type": "repo" if repo else "user",
                    "stars": data.get('stargazers_count', 0) if repo else None,
                    "followers": data.get('followers', 0) if not repo else None
                }
            return {"valid": False, "reason": f"GitHub API Status {response.status_code}"}
        except Exception as e:
            return {"valid": False, "reason": str(e)}

    async def validate_all_links_async(self, resume_text: str) -> Dict:
        links_data = self.extract_all_link_formats(resume_text)
        results = {
            "total_links_found": len(links_data),
            "links": {},
            "summary": {"working": 0, "broken": 0, "unable_to_verify": 0}
        }

        print("[Link Validator] Total Links Extracted:", results["total_links_found"])
        
        async with httpx.AsyncClient() as client:
            for link_info in links_data:
                original = link_info["original"]
                normalized = link_info["normalized"]
                
                if not self.is_valid_url_format(normalized):
                    results["links"][original] = {
                        "normalized_url": normalized,
                        "valid": False,
                        "reason": "Invalid URL format",
                        "type": link_info["type"]}
                    results["summary"]["unable_to_verify"] += 1
                    continue
                
                if "github.com" in normalized:
                    validation = await self.validate_github_profile_async(client, normalized)
                    validation.update({"normalized_url": normalized, "type": "github", "original_text": original})
                    results["links"][original] = validation
                else:
                    is_active, status, _ = await self.check_link_active_async(client, normalized)
                    results["links"][original] = {
                        "normalized_url": normalized,
                        "valid": is_active,
                        "status_code": status,
                        "type": "design_portfolio" if any(d in normalized for d in ["behance", "dribbble"]) else link_info["type"],
                        "original_text": original
                    }
                
                if results["links"][original].get("valid"):
                    results["summary"]["working"] += 1
                else:
                    results["summary"]["broken"] += 1
                    
        return results