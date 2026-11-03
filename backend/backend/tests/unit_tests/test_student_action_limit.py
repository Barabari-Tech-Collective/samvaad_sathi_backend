"""The per-user action ceiling must trip at its limit and be scoped per user and action."""
import fastapi
import pytest

from src.api.dependencies import rate_limit

pytestmark = pytest.mark.rate_limited


@pytest.mark.asyncio
async def test_limit_trips_per_user_and_action(monkeypatch):
    rate_limit._LOCAL_HITS.clear()

    class _Down:  # force the in-process fallback so the test needs no Redis
        client = type("C", (), {"incr": staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))})()

    monkeypatch.setattr(rate_limit, "async_redis", _Down)
    check = rate_limit.student_action_limit("unit_test_action", limit=2)
    user = type("U", (), {"id": 101})()
    other = type("U", (), {"id": 102})()
    await check(current_user=user)
    await check(current_user=user)
    with pytest.raises(fastapi.HTTPException) as caught:
        await check(current_user=user)
    assert caught.value.status_code == 429
    await check(current_user=other)  # another student is unaffected
