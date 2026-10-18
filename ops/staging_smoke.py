"""Small, dependency-free staging smoke check for Gate 6.

This deliberately checks only public, non-mutating contracts. It is safe to
run after a Render deploy and fails closed when the service is healthy but the
Practice API was not mounted.

Usage::

    python ops/staging_smoke.py https://staging.example.com
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


REQUIRED_PATHS = {
    "/health",
    "/openapi.json",
    "/audio/attempts/{question_attempt_id}/transcription",
}


def fetch_json(base_url: str, path: str) -> object:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Accept": "application/json", "User-Agent": "samvaad-gate6-smoke/1"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError(f"{path} returned HTTP {response.status}")
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url", help="Staging base URL, e.g. https://staging.example.com")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    try:
        health = fetch_json(base_url, "/health")
        if not isinstance(health, dict):
            raise RuntimeError("/health did not return a JSON object")
        openapi = fetch_json(base_url, "/openapi.json")
        if not isinstance(openapi, dict):
            raise RuntimeError("/openapi.json did not return a JSON object")
        paths = set((openapi.get("paths") or {}).keys())
        missing = sorted(REQUIRED_PATHS - paths)
        if missing:
            raise RuntimeError(f"OpenAPI is missing required paths: {', '.join(missing)}")
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
        print(f"Gate 6 smoke failed: {error}", file=sys.stderr)
        return 1

    print(f"Gate 6 smoke passed for {base_url}: health and {len(paths)} API paths present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
