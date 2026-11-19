"""Every backend route, as a snapshot the dashboard checks its API calls against.

The dashboard repo keeps a copy (tests/fixtures/backend_routes.json) and fails CI when it
calls a path that is not here. Regenerate after adding or removing a route with
ROUTE_SNAPSHOT_WRITE=1, then copy the file to the dashboard repo.
"""
import json
import os
import pathlib

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "backend_routes.json"


def _routes():
    from src.main import backend_app

    return sorted({
        f"{method} {route.path}"
        for route in backend_app.routes
        if hasattr(route, "methods")
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    })


def test_route_snapshot_is_current():
    current = _routes()
    if os.environ.get("ROUTE_SNAPSHOT_WRITE") == "1":
        FIXTURE.write_text(json.dumps(current, indent=1) + "\n")
    assert current == json.loads(FIXTURE.read_text())
