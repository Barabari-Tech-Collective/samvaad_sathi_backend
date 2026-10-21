"""Read-only public API contract smoke; does not certify authenticated readiness.

Usage: python ops/staging_smoke.py https://staging.example.com --report smoke.json
The base URL is the origin. API_PREFIX defaults to /api; docs remain at the root.
A deployment with intentionally disabled API docs cannot pass this staging check.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class SmokeError(RuntimeError):
    """Contains only safe diagnostics, never response bodies or input URLs."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SmokeError("redirect_rejected")


def validate_origin(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or any(c.isspace() or ord(c) < 32 or c == "\\" for c in value)
            or port == 0
        ):
            raise ValueError
    except ValueError:
        raise SmokeError("invalid_https_origin") from None
    return value.rstrip("/")


def validate_prefix(value: str) -> str:
    if value == "":
        return value
    if (
        not value.startswith("/")
        or value.endswith("/")
        or any(
            not part
            or not all(c.isascii() and (c.isalnum() or c in "-_") for c in part)
            for part in value[1:].split("/")
        )
    ):
        raise SmokeError("invalid_api_prefix")
    return value


def validate_health_url(value: str) -> tuple[str, str]:
    try:
        parsed = urllib.parse.urlsplit(value)
        if (
            parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or not parsed.path.endswith("/health")
            or any(c.isspace() or ord(c) < 32 or c == "\\" for c in value)
        ):
            raise ValueError
        origin = validate_origin(f"{parsed.scheme}://{parsed.netloc}")
        validate_prefix(parsed.path[: -len("/health")])
    except (ValueError, SmokeError):
        raise SmokeError("invalid_https_health_url") from None
    return origin, parsed.path


def validate_health(health: object) -> None:
    if (
        not isinstance(health, dict)
        or health.get("status") != "healthy"
        or health.get("service") != "samvaad-sathi-backend"
    ):
        raise SmokeError("health_contract_failed")


def fetch_json(
    origin: str,
    path: str,
    *,
    timeout: int = 15,
    max_bytes: int = MAX_RESPONSE_BYTES,
    user_agent: str = "Samvaad-Public-Contract-Smoke/2.0",
    use_proxy: bool = True,
    bearer_token: str | None = None,
) -> object:
    request = urllib.request.Request(
        origin + path,
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
    )
    if bearer_token is not None:
        request.add_header("Authorization", "Bearer " + bearer_token)
    handlers = [NoRedirect()]
    if not use_proxy:
        handlers.append(urllib.request.ProxyHandler({}))
    try:
        with urllib.request.build_opener(*handlers).open(
            request, timeout=timeout
        ) as response:
            if response.status != 200:
                raise SmokeError("unexpected_http_status")
            if response.headers.get_content_type() != "application/json":
                raise SmokeError("unexpected_content_type")
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise SmokeError("response_too_large")
            return json.loads(payload)
    except SmokeError:
        raise
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        raise SmokeError(f"http_{code}") from None
    except (
        OSError,
        ValueError,
        urllib.error.URLError,
        http.client.HTTPException,
        RecursionError,
    ):
        raise SmokeError("network_or_invalid_json") from None


def run(origin: str, prefix: str) -> dict:
    origin = validate_origin(origin)
    prefix = validate_prefix(prefix)
    health = fetch_json(origin, prefix + "/health")
    validate_health(health)
    schema = fetch_json(origin, "/openapi.json")
    if (
        not isinstance(schema, dict)
        or not isinstance(schema.get("openapi"), str)
        or not schema["openapi"].startswith("3.")
    ):
        raise SmokeError("invalid_openapi_document")
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        raise SmokeError("invalid_openapi_paths")
    for path in (
        prefix + "/health",
        prefix + "/audio/attempts/{question_attempt_id}/transcription",
    ):
        item = paths.get(path)
        operation = item.get("get") if isinstance(item, dict) else None
        responses = operation.get("responses") if isinstance(operation, dict) else None
        if not isinstance(responses, dict) or not isinstance(
            responses.get("200"), dict
        ):
            raise SmokeError("required_get_contract_missing")
    return {"target_origin": origin, "api_path_count": len(paths), "api_prefix": prefix}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "base_url", help="HTTPS origin, without credentials or API path"
    )
    parser.add_argument("--api-prefix", default="/api")
    parser.add_argument(
        "--report",
        type=Path,
        help="JSON evidence file; contains validated origin and outcome, never response bodies",
    )
    args = parser.parse_args(argv)
    report = {
        "check": "public_api_contract",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "passed": False,
        "authenticated_acceptance": "not_checked",
    }
    try:
        report.update(run(args.base_url, args.api_prefix))
        report["passed"] = True
    except SmokeError as error:
        report["error"] = str(error)
    if args.report:
        try:
            args.report.write_text(json.dumps(report, indent=2) + "\n")
        except OSError:
            print("Public API smoke failed: evidence_write_failed", file=sys.stderr)
            return 1
    print(json.dumps(report), file=sys.stdout if report["passed"] else sys.stderr)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
