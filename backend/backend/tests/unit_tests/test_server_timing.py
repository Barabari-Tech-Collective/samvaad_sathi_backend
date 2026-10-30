"""Server-Timing lists each timed step and the total; untimed requests still get a total."""

import fastapi
from fastapi.testclient import TestClient

from src.utilities import server_timing
from src.utilities.server_timing import step


def _client():
    app = fastapi.FastAPI()
    server_timing.install(app)

    @app.get("/timed")
    async def timed():
        with step("central_auth"):
            pass
        with step("redis_revoked"):
            pass
        return {}

    @app.get("/plain")
    async def plain():
        return {}

    return TestClient(app)


def test_steps_and_total_are_reported():
    header = _client().get("/timed").headers["server-timing"]
    names = [part.split(";")[0] for part in header.split(", ")]
    assert names == ["central_auth", "redis_revoked", "total"]


def test_untimed_request_reports_total_only():
    assert _client().get("/plain").headers["server-timing"].startswith("total;dur=")


def test_step_is_a_no_op_outside_a_request():
    with step("anything"):
        pass
