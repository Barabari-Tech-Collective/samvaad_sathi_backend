"""Retired v1 routes.

Every product frontend now calls the v2 surface and central authentication only. These
v1 endpoints are no longer mounted, but their code stays in place so a rollback or a
later clean-up is a one-line edit: delete (or re-add) the entry below.

Entries are (HTTP method, router path without the API prefix).
"""

RETIRED_V1_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        # Second authentication system. Sign-in is central-only, so no local password
        # registration, login or refresh-token minting is exposed.
        ("POST", "/users"),
        ("POST", "/login"),
        ("POST", "/refresh"),
        # Superseded by /v2/analytics and /v2/summary-report. No frontend calls them.
        ("GET", "/analytics/segment/role"),
        ("GET", "/analytics/segment/difficulty"),
        ("GET", "/analytics/segment/college"),
        ("GET", "/analytics/system"),
        ("GET", "/analytics/scoring"),
        ("POST", "/analytics/report-engagement"),
        ("GET", "/summary-reports"),
        ("GET", "/summary-report/{interview_id}"),
        # Standalone analysers no frontend calls (analysis happens in the v2 pipeline).
        ("POST", "/analyze-pace"),
        ("POST", "/analyze-pause"),
        ("POST", "/communication-based-analysis"),
        ("POST", "/domain-base-analysis"),
        ("GET", "/get_knowledgeset"),
    }
)


def drop_retired_routes(router) -> int:
    """Remove retired (method, path) pairs from ``router``; return how many were dropped."""
    kept, dropped = [], 0
    for route in router.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        if any((m, path) in RETIRED_V1_ROUTES for m in methods):
            dropped += 1
            continue
        kept.append(route)
    router.routes[:] = kept
    return dropped
