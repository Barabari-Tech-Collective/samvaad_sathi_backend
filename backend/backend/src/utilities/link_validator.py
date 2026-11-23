import re
from typing import Dict, List, Tuple, Any, Optional
from urllib.parse import urlparse, urlunparse, unquote
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

    def __init__(self, timeout: int = 8):
        self.timeout = timeout
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        self.platform_catalog = {
            # Code Repositories
            "github.com": {"platform": "github", "category": "repository"},
            "gitlab.com": {"platform": "gitlab", "category": "repository"},
            "bitbucket.org": {"platform": "bitbucket", "category": "repository"},
            # Competitive Programming / Coding Practice
            "leetcode.com": {"platform": "leetcode", "category": "coding_profile"},
            "hackerrank.com": {"platform": "hackerrank", "category": "coding_profile"},
            "codechef.com": {"platform": "codechef", "category": "coding_profile"},
            "codeforces.com": {"platform": "codeforces", "category": "coding_profile"},
            "geeksforgeeks.org": {"platform": "geeksforgeeks", "category": "coding_profile"},
            "codewars.com": {"platform": "codewars", "category": "coding_profile"},
            # Portfolios & Builders
            "lovable.app": {"platform": "lovable", "category": "portfolio"},
            "carrd.co": {"platform": "carrd", "category": "portfolio"},
            "bento.me": {"platform": "bento", "category": "portfolio"},
            "readcv.com": {"platform": "readcv", "category": "portfolio"},
            "polywork.com": {"platform": "polywork", "category": "portfolio"},
            "linktr.ee": {"platform": "linktree", "category": "portfolio"},
            "framer.website": {"platform": "framer", "category": "portfolio"},
            "framer.app": {"platform": "framer", "category": "portfolio"},
            "webflow.io": {"platform": "webflow", "category": "portfolio"},
            # Deployments & Hosting Platforms
            "vercel.app": {"platform": "vercel", "category": "deployment"},
            "netlify.app": {"platform": "netlify", "category": "deployment"},
            "render.com": {"platform": "render", "category": "deployment"},
            "onrender.com": {"platform": "render", "category": "deployment"},
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
            # Data, research, and business work samples
            "kaggle.com": {"platform": "kaggle", "category": "portfolio"},
            "public.tableau.com": {"platform": "tableau", "category": "dashboard"},
            "app.powerbi.com": {"platform": "power_bi", "category": "dashboard"},
            "notion.site": {"platform": "notion", "category": "portfolio"},
            # Professional Networks & Socials
            "linkedin.com": {"platform": "linkedin", "category": "social"},
            "twitter.com": {"platform": "twitter", "category": "social"},
            "x.com": {"platform": "twitter", "category": "social"},
            "medium.com": {"platform": "medium", "category": "blog"},
            "youtube.com": {"platform": "youtube", "category": "social"},
            # Credentials & Packages
            "npmjs.com": {"platform": "npm", "category": "package"},
            "pypi.org": {"platform": "pypi", "category": "package"},
            "credly.com": {"platform": "credly", "category": "certification"},
            "badgr.com": {"platform": "badgr", "category": "certification"},
            "tinyurl.com": {"platform": "tinyurl", "category": "shortener"},
            "bit.ly": {"platform": "bitly", "category": "shortener"},
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
            match = re.search(
                r"[?&](?:q|url)=(https?%3A%2F%2F[^\s&]+|https?://[^\s&]+)",
                clean,
                re.IGNORECASE,
            )
            if match:
                clean = unquote(match.group(1))

        # Handle mailto links or plain unquoted URIs
        clean = unquote(clean).strip()
        return clean

    def _normalize_candidate_url(self, raw_url: str) -> str:
        """Return a safe, canonical recruiter-facing URL or an empty string.

        Resume parsers occasionally emit contact links as ``https://mailto:``
        or include punctuation next to a URL. Those values must never be sent
        over the network or presented as verified work evidence.
        """
        clean = self._unwrap_url(raw_url).strip(" \t\r\n<>[](){}\"',.;")
        lowered = clean.casefold()
        if not clean or lowered.startswith("mailto:") or re.match(
            r"^https?://mailto:", lowered
        ):
            return ""
        if re.fullmatch(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", clean):
            return ""
        if not lowered.startswith(("http://", "https://")):
            clean = f"https://{clean}"

        try:
            parsed = urlparse(clean)
        except ValueError:
            return ""
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        if hostname.startswith("www."):
            hostname = hostname[4:]
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not hostname
            or "." not in hostname
            or any(character.isspace() for character in clean)
        ):
            return ""

        try:
            port = f":{parsed.port}" if parsed.port else ""
        except ValueError:
            return ""
        normalized_path = parsed.path or ""
        if normalized_path != "/":
            normalized_path = normalized_path.rstrip("/")
        scheme = "https" if parsed.scheme.casefold() in {"http", "https"} else parsed.scheme.casefold()
        return urlunparse(
            (
                scheme,
                f"{hostname}{port}",
                normalized_path,
                "",
                parsed.query,
                "",
            )
        )

    def classify_url(self, url: str) -> Dict[str, str]:
        """
        Classifies unwrapped URLs into standard platform and category maps.
        """
        unwrapped = self._unwrap_url(url)
        clean_url = unwrapped.lower().strip()

        if not clean_url.startswith(("http://", "https://", "mailto:")):
            clean_url = "https://" + clean_url

        if clean_url.startswith("mailto:") or re.match(
            r"^https?://mailto:", clean_url
        ):
            return {"platform": "email", "category": "contact"}

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
        blocked_system_domains = {
            "google.com",
            "google.co.in",
            "whatsapp.com",
            "gmail.com",
        }
        if any(b in hostname for b in blocked_system_domains):
            return {"platform": "system_link", "category": "other"}

        # 3. Custom deployment / live demo heuristic (e.g. interview-simulator-demo.com, code-engine-demo.com)
        if "demo" in hostname or "app" in hostname or "dev" in hostname:
            return {"platform": "custom_deployment", "category": "deployment"}

        # 4. Custom Personal Domain / Portfolio
        if "." in hostname:
            return {"platform": "custom_domain", "category": "portfolio"}

        return {"platform": "unknown", "category": "other"}

    async def check_link_active_async(
        self, client: httpx.AsyncClient, url: str
    ) -> Tuple[bool, int, str]:
        """Non-blocking async active check."""
        clean_url = self._unwrap_url(url)
        if not clean_url.startswith(("http://", "https://")):
            clean_url = "https://" + clean_url

        try:
            response = await client.head(
                clean_url,
                timeout=self.timeout,
                follow_redirects=True,
                headers=self.headers,
            )
            if is_success_or_blocked(response.status_code):
                return True, response.status_code, ""

            response = await client.get(
                clean_url,
                timeout=self.timeout,
                follow_redirects=True,
                headers=self.headers,
            )
            return is_success_or_blocked(response.status_code), response.status_code, ""
        except Exception as e:
            return False, 0, str(e)

    async def validate_github_url_async(
        self, client: httpx.AsyncClient, url: str
    ) -> Dict[str, Any]:
        """Real-world browser-aligned validation for GitHub repositories and profiles."""
        clean_url = self._unwrap_url(url).strip()
        if not clean_url.startswith(("http://", "https://")):
            clean_url = "https://" + clean_url

        match = re.search(r"github\.com/([^/]+)/?([^/]*)", clean_url)
        if not match:
            return {
                "valid": False,
                "status_code": 400,
                "reason": "Invalid GitHub URL format",
            }

        username, raw_repo = match.group(1).strip(), match.group(2).strip()
        if username.lower() in ["settings", "notifications", "marketplace", "explore"]:
            return {"valid": False, "status_code": 404, "reason": "System path"}

        # Build candidate URLs to test (handling spaces or formatting quirks from PDF extraction)
        candidates = [clean_url]
        if " " in clean_url:
            candidates.append(clean_url.replace(" ", "-"))
            candidates.append(clean_url.replace(" ", "_"))
            candidates.append(clean_url.replace(" ", "%20"))

        working_url = None

        # Step 1: Real-world Web Check with browser headers and follow_redirects=True
        # This mirrors EXACTLY what the user experiences in their browser!
        for test_url in candidates:
            try:
                res = await client.get(
                    test_url,
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers=self.headers,
                )
                if res.status_code == 200:
                    working_url = str(res.url) if res.url else test_url
                    break
            except Exception:
                pass

        # Step 2: If web check succeeded, it is VERIFIED LIVE!
        if working_url:
            return {
                "valid": True,
                "status_code": 200,
                "url": working_url,
                "sub_type": "repository" if raw_repo else "profile",
                "reason": "Repository is publicly accessible and verified live.",
            }

        # Step 3: If web check did not return 200, try GitHub API as secondary check
        api_repo_slug = raw_repo.replace(" ", "-") if raw_repo else ""
        api_url = (
            f"https://api.github.com/repos/{username}/{api_repo_slug}"
            if api_repo_slug
            else f"https://api.github.com/users/{username}"
        )
        try:
            api_res = await client.get(
                api_url,
                timeout=self.timeout,
                follow_redirects=True,
                headers=self.headers,
            )
            if api_res.status_code == 200:
                data = api_res.json() if hasattr(api_res, "json") else {}
                return {
                    "valid": True,
                    "status_code": 200,
                    "sub_type": "repository" if raw_repo else "profile",
                    "stars": data.get("stargazers_count", 0) if raw_repo else None,
                    "followers": data.get("followers", 0) if not raw_repo else None,
                    "reason": "Verified via GitHub API.",
                }
        except Exception:
            pass

        # Step 4: Link is NOT accessible in the browser. Diagnose the EXACT reason:
        # Check if user profile exists on GitHub
        user_exists = False
        try:
            user_web_res = await client.get(
                f"https://github.com/{username}",
                timeout=self.timeout,
                follow_redirects=True,
                headers=self.headers,
            )
            if user_web_res.status_code == 200:
                user_exists = True
        except Exception:
            pass

        if not user_exists:
            # Fallback to API user check
            try:
                user_api_res = await client.get(
                    f"https://api.github.com/users/{username}",
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers=self.headers,
                )
                if user_api_res.status_code == 200:
                    user_exists = True
            except Exception:
                pass

        if raw_repo and user_exists:
            # User profile exists, but repository is 404 -> Private repository!
            return {
                "valid": False,
                "status_code": 404,
                "sub_type": "repository",
                "user_exists": True,
                "is_private_or_missing_repo": True,
                "reason": (
                    f"GitHub account '{username}' exists, but this repository cannot be viewed publicly (404 Not Found). "
                    "The repository is likely set to Private or the repository name has a typo."
                ),
            }

        if " " in clean_url:
            return {
                "valid": False,
                "status_code": 400,
                "sub_type": "repository" if raw_repo else "profile",
                "user_exists": user_exists,
                "is_private_or_missing_repo": False,
                "reason": f"The repository URL contains invalid spaces ('{clean_url}'). Ensure the link is properly formatted.",
            }

        return {
            "valid": False,
            "status_code": 404,
            "sub_type": "repository" if raw_repo else "profile",
            "user_exists": False,
            "is_private_or_missing_repo": False,
            "reason": f"GitHub repository or user '{username}' returned 404 Not Found.",
        }

    async def validate_all_links_async(
        self, extracted_links: List[Any]
    ) -> Dict[str, Any]:
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
                        "line": item.get("line"),
                    }
                else:
                    continue

                normalized_url = self._normalize_candidate_url(raw_url)
                classification = self.classify_url(normalized_url)

                # Contact details and parser/system links are not professional
                # evidence. Keep them out of both network checks and the UI.
                if (
                    normalized_url
                    and classification["category"] != "contact"
                    and classification["platform"] != "system_link"
                    and normalized_url not in url_metadata_map
                ):
                    url_metadata_map[normalized_url] = meta

        results = {
            "total_links_found": len(url_metadata_map),
            "links": {},
            "summary": {"working": 0, "broken": 0, "repositories": 0, "deployments": 0},
        }

        async with httpx.AsyncClient() as client:
            for clean_url, spatial_meta in url_metadata_map.items():
                classification = self.classify_url(clean_url)
                cat = classification["category"]
                platform = classification["platform"]

                if platform == "github":
                    gh_meta = await self.validate_github_url_async(client, clean_url)
                    is_valid = gh_meta.get("valid", False)
                    status_code = gh_meta.get("status_code", 404)
                    extra_meta = gh_meta
                elif platform == "linkedin":
                    # LinkedIn blocks automated requests with 999, so we validate format
                    is_format_valid = bool(
                        re.match(
                            r"^https?:\/\/(www\.|[a-z]{2}\.)?linkedin\.com\/(in|company|school)\/[a-zA-Z0-9%_-]+\/?$",
                            clean_url,
                            re.IGNORECASE,
                        )
                    )
                    is_valid, status_code, err_msg = await self.check_link_active_async(
                        client, clean_url
                    )
                    # Only mark as valid if format is correct AND it didn't return an actual 404
                    if is_format_valid and (is_valid or status_code in (401, 403, 999)):
                        is_valid = True
                        err_msg = ""
                    elif not is_format_valid:
                        is_valid = False
                        err_msg = "Invalid LinkedIn URL format"
                    extra_meta = {"error": err_msg} if not is_valid and err_msg else {}
                else:
                    is_valid, status_code, err_msg = await self.check_link_active_async(
                        client, clean_url
                    )
                    is_sleeping = (
                        status_code in (502, 503, 504)
                        or any(k in str(err_msg).lower() for k in ("timeout", "timed out", "connecterror"))
                    )
                    extra_meta = {"is_sleeping_or_timeout": is_sleeping}
                    if err_msg:
                        extra_meta["error"] = err_msg

                link_record = {
                    "url": clean_url,
                    "valid": is_valid,
                    "status_code": status_code,
                    "platform": platform,
                    "category": cat,
                    "page": spatial_meta.get("page"),
                    "section": spatial_meta.get("section"),
                    "anchorText": spatial_meta.get("anchorText"),
                    "line": spatial_meta.get("line"),
                    **extra_meta,
                }

                results["links"][clean_url] = link_record

                if is_valid:
                    results["summary"]["working"] += 1
                else:
                    results["summary"]["broken"] += 1

                if cat == "repository":
                    results["summary"]["repositories"] += 1
                elif cat == "deployment":
                    results["summary"]["deployments"] += 1

        return results
