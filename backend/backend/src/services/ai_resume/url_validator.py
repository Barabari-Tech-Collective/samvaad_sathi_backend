import re
import httpx
import asyncio
from loguru import logger

# Regex to find common URLs in resume text, including specific professional domains without http
URL_PATTERN = re.compile(r'https?://[^\s<>"]+|www\.[^\s<>"]+|(?:github\.com|linkedin\.com/in|gitlab\.com|bitbucket\.org)/[^\s<>"]+')

# We will focus validation primarily on professional profiles.
PROFESSIONAL_DOMAINS = ["github.com", "linkedin.com", "gitlab.com", "bitbucket.org"]

def extract_professional_urls(text: str) -> list[str]:
    """
    Extracts URLs from text and filters for professional profiles.
    Returns a list of URLs prefixed with https:// if necessary.
    """
    raw_urls = URL_PATTERN.findall(text)
    professional_urls = []
    
    for url in raw_urls:
        url_lower = url.lower()
        # Check if the URL belongs to a professional domain
        if any(domain in url_lower for domain in PROFESSIONAL_DOMAINS):
            # Ensure it has a scheme
            if not url_lower.startswith("http"):
                url = "https://" + url
            # Clean trailing punctuation
            url = url.rstrip('.,;)')
            professional_urls.append(url)
            
    return list(set(professional_urls))

async def validate_urls(urls: list[str]) -> bool:
    """
    Validates a list of URLs concurrently.
    Returns True if AT LEAST ONE URL is considered working.
    Returns False if NO URLs are provided, or ALL URLs return 404.
    """
    if not urls:
        logger.warning("No professional URLs found for validation.")
        return False
        
    async def check_url(url: str, client: httpx.AsyncClient) -> bool:
        try:
            # We use a standard browser user agent to avoid basic blocks
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5"
            }
            # GET request because some servers block HEAD
            response = await client.get(url, headers=headers, follow_redirects=True, timeout=10.0)
            
            # 404 Not Found specifically means the profile doesn't exist.
            # 200 means success.
            # 403 / 999 means bot protection triggered (e.g. LinkedIn), but the profile likely exists.
            if response.status_code == 404:
                logger.warning(f"URL {url} returned 404 Not Found.")
                return False
                
            logger.info(f"URL {url} returned {response.status_code}. Considered valid.")
            return True
            
        except Exception as e:
            logger.warning(f"Error validating {url}: {str(e)}")
            # If we get a network error, DNS error, etc., we treat it as invalid.
            return False

    async with httpx.AsyncClient(verify=False) as client: # verify=False for self-signed or picky sites occasionally
        tasks = [check_url(u, client) for u in urls]
        results = await asyncio.gather(*tasks)
        
    # If at least one link is working, we allow the resume.
    return any(results)
