"""Validate only the direct loopback service's liveness, without proxy or redirects."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
import urllib.parse

_spec = importlib.util.spec_from_file_location(
    "samvaad_probe", Path(__file__).with_name("staging_smoke.py")
)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def validate_target(value: str) -> tuple[str, str]:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
        if (
            parsed.scheme != "http"
            or port is None
            or port == 0
            or parsed.netloc != f"127.0.0.1:{port}"
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or not parsed.path.endswith("/health")
            or any(c.isspace() or ord(c) < 32 or c == "\\" for c in value)
        ):
            raise ValueError
        probe.validate_prefix(parsed.path[: -len("/health")])
    except (ValueError, probe.SmokeError):
        raise probe.SmokeError("invalid_loopback_health_url") from None
    return f"http://{parsed.netloc}", parsed.path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("health_url")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        origin, path = validate_target(args.health_url)
        if not args.validate_only:
            probe.validate_health(
                probe.fetch_json(
                    origin,
                    path,
                    timeout=5,
                    max_bytes=64 * 1024,
                    user_agent="Samvaad-Local-Release-Health-Check/1.0",
                    use_proxy=False,
                )
            )
    except probe.SmokeError as error:
        print(f"Local health check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
