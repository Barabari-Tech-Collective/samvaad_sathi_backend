import re
from typing import Dict, List, Tuple, Any, Optional
from urllib.parse import urlparse, unquote
import httpx


def is_success_or_blocked(status: int) -> bool:
    # 401/403 = link resolves but requires auth (e.g. private GitHub repo).
    # We treat these as "present" — the URL exists even if the recruiter
    # can't open it — to avoid unfairly penalising candidates.
    return status < 400 or status in (401, 403)


class SmartLinkValidator:
    """
    Pure Validation & Classification Engine.
    Unwraps proxy/search redirect wrappers (e.g. Google Search URLs),
    validates network integrity, and categorizes links by platform and category.
    """

    def __init__(self, timeout: int = 4):
        self.timeout = timeout
        self.headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            )
        }

        self.platform_catalog = {
            # Code Repositories
            "github.com": {"platform": "github", "category": "repository"},
            "gitlab.com": {"platform": "gitlab", "category": "repository"},
            "bitbucket.org": {"platform": "bitbucket", "category": "repository"},

            # Deployments & Hosting Platforms
            "vercel.app": {"platform": "vercel", "category": "deployment"},
            "netlify.app": {"platform": "netlify", "category": "deployment"},
            "render.com": {"platform": "render", "category": "deployment"},
            "railway.app": {"platform": "railway", "category": "deployment"},
            "fly.dev": {"platform": "fly.io", "category": "deployment"},
            "herokuapp.com": {"platform": "heroku", "category": "deployment"},
            "github.io": {"platform": "github_pages", "category": "deployment"},
            "pages.dev": {"platform": "cloudflare_pages", "category": "deployment"},
            "amplifyapp.com": {"platform": "aws_amplify", "category": "deployment"},
            "firebaseapp.com": {"platform": "firebase", "category": "deployment"},
            "web.app": {"platform": "firebase", "category": "deployment"},

            # Design & Creative
            "figma.com": {"platform": "figma", "category": "design"},
            "behance.net": {"platform": "behance", "category": "design"},
            "dribbble.com": {"platform": "dribbble", "category": "design"},

            # Professional Networks & Socials
            "linkedin.com": {"platform": "linkedin", "category": "social"},
            "twitter.com": {"platform": "twitter", "category": "social"},
            "x.com": {"platform": "twitter", "category": "social"},
            "medium.com": {"platform": "medium", "category": "blog"},
            "youtube.com": {"platform": "youtube", "category": "social"},

            # Coding Platforms
            "leetcode.com": {"platform": "leetcode", "category": "coding_profile"},
            "hackerrank.com": {"platform": "hackerrank", "category": "coding_profile"},
            "codechef.com": {"platform": "codechef", "category": "coding_profile"},
            "codeforces.com": {"platform": "codeforces", "category": "coding_profile"},
            "geeksforgeeks.org": {"platform": "geeksforgeeks", "category": "coding_profile"},

            # Credentials & Packages
            "npmjs.com": {"platform": "npm", "category": "package"},
            "pypi.org": {"platform": "pypi", "category": "package"},
            "credly.com": {"platform": "credly", "category": "certification"},
            "badgr.com": {"platform": "badgr", "category": "certification"},
            "tinyurl.com": {"platform": "tinyurl", "category": "shortener"},
            "bit.ly": {"platform": "bitly", "category": "shortener"}
        }

    def _unwrap_url(self, raw_url: str) -> str:
        """
        Strips Google search query wrappers, redirect proxies, and unquotes URL encodings.
        Example:
        'https://www.google.com/search?q=https://github.com/user/repo'
        -> 'https://github.com/user/repo'
        """
        if not raw_url or not isinstance(raw_url, str):
            return ""

        clean = raw_url.strip()

        # Unwrap Google search/url redirects
        if "google.com/search" in clean or "google.com/url" in clean:
            match = re.search(r'[?&](?:q|url)=(https?%3A%2F%2F[^\s&]+|https?://[^\s&]+)', clean, re.IGNORECASE)
            if match:
                clean = unquote(match.group(1))

        # Handle mailto links or plain unquoted URIs
        clean = unquote(clean).strip()
        return clean

    def classify_url(self, url: str) -> Dict[str, str]:
        """
        Classifies unwrapped URLs into standard platform and category maps.
        """
        unwrapped = self._unwrap_url(url)
        clean_url = unwrapped.lower().strip()

        if not clean_url.startswith(("http://", "https://", "mailto:")):
            clean_url = "https://" + clean_url

        try:
            parsed = urlparse(clean_url)
            hostname = parsed.hostname or ""
            if hostname.startswith("www."):
                hostname = hostname[4:]
        except Exception:
            hostname = clean_url

        # 1. Match known platform rules
        for domain, meta in self.platform_catalog.items():
            if domain in hostname:
                return meta

        # 2. Block system / search domains from triggering false portfolio flags
        blocked_system_domains = {"google.com", "google.co.in", "whatsapp.com", "gmail.com"}
        if any(b in hostname for b in blocked_system_domains):
            return {"platform": "system_link", "category": "other"}

        # 3. Custom deployment / live demo heuristic (e.g. interview-simulator-demo.com, code-engine-demo.com)
        if "demo" in hostname or "app" in hostname or "dev" in hostname:
            return {"platform": "custom_deployment", "category": "deployment"}

        # 4. Custom Personal Domain / Portfolio
        if "." in hostname:
            return {"platform": "custom_domain", "category": "other"}

        return {"platform": "unknown", "category": "other"}

    async def check_link_active_async(self, client: httpx.AsyncClient, url: str) -> Tuple[bool, int, str]:
        """Non-blocking async active check."""
        clean_url = self._unwrap_url(url)
        if not clean_url.startswith(("http://", "https://")):
            clean_url = "https://" + clean_url

        try:
            response = await client.head(clean_url, timeout=self.timeout, follow_redirects=True, headers=self.headers)
            if is_success_or_blocked(response.status_code):
                return True, response.status_code, ""

            response = await client.get(clean_url, timeout=self.timeout, follow_redirects=True, headers=self.headers)
            return is_success_or_blocked(response.status_code), response.status_code, ""
        except Exception as e:
            return False, 0, str(e)

    async def validate_github_url_async(self, client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
        """GitHub API validation for profiles and repositories."""
        clean_url = self._unwrap_url(url)
        if not clean_url.startswith(("http://", "https://")):
            clean_url = "https://" + clean_url

        match = re.search(r'github\.com/([^/]+)/?([^/]*)', clean_url)
        if not match:
            return {"valid": False, "status_code": 400, "reason": "Invalid GitHub URL format"}

        username, repo = match.group(1), match.group(2)
        if username.lower() in ["settings", "notifications", "marketplace", "explore"]:
            return {"valid": False, "status_code": 404, "reason": "System path"}

        api_url = f"https://api.github.com/repos/{username}/{repo}" if repo else f"https://api.github.com/users/{username}"

        try:
            response = await client.get(api_url, timeout=self.timeout)
            status_code = response.status_code
            if status_code == 200:
                data = response.json()
                return {
                    "valid": True,
                    "status_code": 200,
                    "sub_type": "repository" if repo else "profile",
                    "stars": data.get('stargazers_count', 0) if repo else None,
                    "followers": data.get('followers', 0) if not repo else None
                }
            elif status_code == 403 or status_code == 401:
                # Only fall back on rate-limit/auth (403/401). For anything else (404, etc.),
                # the GitHub API result is authoritative — trust it.
                is_valid, fallback_status, err = await self.check_link_active_async(client, clean_url)
                return {
                    "valid": is_valid, 
                    "status_code": fallback_status, 
                    "sub_type": "repository" if repo else "profile",
                    "reason": err or f"Fallback {fallback_status}"
                }
            else:
                return {"valid": False, "status_code": status_code, "reason": f"GitHub Status {status_code}"}
        except Exception as e:
            # Fallback on exception as well
            is_valid, fallback_status, err = await self.check_link_active_async(client, clean_url)
            return {
                "valid": is_valid, 
                "status_code": fallback_status, 
                "sub_type": "repository" if repo else "profile",
                "reason": str(e)
            }
    async def validate_all_links_async(self, extracted_links: List[Any]) -> Dict[str, Any]:
        """
        Processes extracted links safely. Guarantees raw string streams or non-URL tokens
        are rejected before executing network requests.
        """
        if isinstance(extracted_links, str):
            extracted_links = []

        url_metadata_map = {}

        if isinstance(extracted_links, list):
            for item in extracted_links:
                if isinstance(item, str):
                    raw_url = item
                    meta = {}
                elif isinstance(item, dict) and "url" in item:
                    raw_url = item["url"]
                    meta = {
                        "page": item.get("page"),
                        "section": item.get("section"),
                        "anchorText": item.get("anchorText"),
                        "line": item.get("line")
                    }
                else:
                    continue

                # Unwrap proxy/search query before indexing
                unwrapped_url = self._unwrap_url(raw_url)
                
                # STRICT URL FILTER: Must contain a dot or valid URL scheme
                # Prevents single character noise ('A', 'B') from reaching the network validator
                if (
                    unwrapped_url 
                    and len(unwrapped_url) > 3 
                    and ("." in unwrapped_url or ":" in unwrapped_url)
                    and unwrapped_url not in url_metadata_map
                ):
                    url_metadata_map[unwrapped_url] = meta

        results = {
            "total_links_found": len(url_metadata_map),
            "links": {},
            "summary": {"working": 0, "broken": 0, "repositories": 0, "deployments": 0}
        }

        async def validate_single_url(clean_url: str, spatial_meta: Dict[str, Any], client: httpx.AsyncClient) -> Dict[str, Any]:
            classification = self.classify_url(clean_url)
            cat = classification["category"]
            platform = classification["platform"]

            # If it's a custom domain but found in Projects or Experience, it's likely a project deployment, not the main portfolio
            section_lower = str(spatial_meta.get("section", "")).lower()
            if platform == "custom_domain" and any(keyword in section_lower for keyword in ["project", "experience"]):
                cat = "deployment"

            if platform == "github":
                gh_meta = await self.validate_github_url_async(client, clean_url)
                is_valid = gh_meta.get("valid", False)
                status_code = gh_meta.get("status_code", 404)
                extra_meta = gh_meta
            elif platform == "linkedin":
                # LinkedIn blocks automated requests with 999, so we validate format
                is_format_valid = bool(re.match(r'^https?:\/\/(www\.|[a-z]{2}\.)?linkedin\.com\/(in|company|school)\/[a-zA-Z0-9%_-]+\/?$', clean_url, re.IGNORECASE))
                is_valid, status_code, err_msg = await self.check_link_active_async(client, clean_url)
                # Only mark as valid if format is correct AND it didn't return an actual 404
                if is_format_valid and (is_valid or status_code in (401, 403, 999)):
                    is_valid = True
                    err_msg = ""
                elif not is_format_valid:
                    is_valid = False
                    err_msg = "Invalid LinkedIn URL format"
                extra_meta = {"error": err_msg} if not is_valid and err_msg else {}
            else:
                is_valid, status_code, err_msg = await self.check_link_active_async(client, clean_url)
                extra_meta = {"error": err_msg} if err_msg else {}

            return {
                "url": clean_url,
                "valid": is_valid,
                "status_code": status_code,
                "platform": platform,
                "category": cat,
                "page": spatial_meta.get("page"),
                "section": spatial_meta.get("section"),
                "anchorText": spatial_meta.get("anchorText"),
                "line": spatial_meta.get("line"),
                **extra_meta
            }

        import asyncio
        async with httpx.AsyncClient() as client:
            tasks = [validate_single_url(clean_url, meta, client) for clean_url, meta in url_metadata_map.items()]
            link_records = await asyncio.gather(*tasks)

        for record in link_records:
            clean_url = record["url"]
            is_valid = record["valid"]
            cat = record["category"]
            
            results["links"][clean_url] = record
            
            if is_valid:
                results["summary"]["working"] += 1
            else:
                results["summary"]["broken"] += 1

            if cat == "repository":
                results["summary"]["repositories"] += 1
            elif cat == "deployment":
                results["summary"]["deployments"] += 1

        return results