import pytest

from src.worker import queue as queue_module


class _FakePool:
    def __init__(self, should_raise: bool = False):
        self.should_raise = should_raise
        self.enqueued: list[tuple[str, dict]] = []

    async def enqueue_job(self, function_name: str, **kwargs):
        if self.should_raise:
            raise ConnectionError("Redis unreachable")
        self.enqueued.append((function_name, kwargs))


@pytest.fixture(autouse=True)
def _reset_pool_cache():
    queue_module._pool = None
    yield
    queue_module._pool = None


@pytest.mark.asyncio
async def test_enqueue_job_returns_true_on_success(monkeypatch):
    fake_pool = _FakePool()
    monkeypatch.setattr(queue_module, "_get_pool", lambda: _async_return(fake_pool))

    result = await queue_module.enqueue_job("some_task", student_id="s1")

    assert result is True
    assert fake_pool.enqueued == [("some_task", {"student_id": "s1"})]


@pytest.mark.asyncio
async def test_enqueue_job_returns_false_when_pool_unavailable(monkeypatch):
    monkeypatch.setattr(queue_module, "_get_pool", lambda: _async_return(None))

    result = await queue_module.enqueue_job("some_task", student_id="s1")

    assert result is False


@pytest.mark.asyncio
async def test_enqueue_job_returns_false_on_enqueue_error(monkeypatch):
    fake_pool = _FakePool(should_raise=True)
    monkeypatch.setattr(queue_module, "_get_pool", lambda: _async_return(fake_pool))

    result = await queue_module.enqueue_job("some_task", student_id="s1")

    assert result is False


async def _async_return(value):
    return value
