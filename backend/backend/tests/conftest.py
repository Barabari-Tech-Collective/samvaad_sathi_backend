"""Suite-wide isolation from state that outlives a test run."""
import pytest


@pytest.fixture(autouse=True)
def _student_action_limits_off_by_default(request, monkeypatch):
    """Per-user action limits are Redis-backed and survive between runs, so a busy suite would
    trip them for reasons unrelated to what it tests. They are skipped unless a test opts in
    with @pytest.mark.rate_limited; every other limiter keeps its normal behaviour.
    """
    if request.node.get_closest_marker("rate_limited"):
        return
    from src.api.dependencies import rate_limit

    real_enforce = rate_limit._enforce

    async def enforce(key, limit, window_seconds):
        if key.startswith("ratelimit:student:"):
            return None
        return await real_enforce(key, limit, window_seconds)

    monkeypatch.setattr(rate_limit, "_enforce", enforce)


def pytest_configure(config):
    config.addinivalue_line("markers", "rate_limited: run with real per-user action limits")
