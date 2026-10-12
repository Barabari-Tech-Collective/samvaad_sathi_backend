import pytest

from src.worker import queue as queue_module


class _FakeJob:
    """arq returns a Job; enqueue_job reports its id."""

    def __init__(self, job_id: str):
        self.job_id = job_id


class _FakePool:
    def __init__(self, should_raise: bool = False, job_id: str | None = "job-1"):
        self.should_raise = should_raise
        self.job_id = job_id
        self.enqueued: list[tuple[str, dict]] = []

    async def enqueue_job(self, function_name: str, **kwargs):
        if self.should_raise:
            raise ConnectionError("Redis unreachable")
        self.enqueued.append((function_name, kwargs))
        return _FakeJob(self.job_id) if self.job_id else None


@pytest.fixture(autouse=True)
def _reset_pool_cache():
    queue_module._pool = None
    yield
    queue_module._pool = None


@pytest.mark.asyncio
async def test_enqueue_job_returns_the_job_id_on_success(monkeypatch):
    fake_pool = _FakePool()
    monkeypatch.setattr(queue_module, "_get_pool", lambda: _async_return(fake_pool))

    result = await queue_module.enqueue_job("some_task", student_id="s1")

    assert result == "job-1"
    assert fake_pool.enqueued == [("some_task", {"student_id": "s1"})]


@pytest.mark.asyncio
async def test_enqueue_job_returns_none_when_pool_unavailable(monkeypatch):
    monkeypatch.setattr(queue_module, "_get_pool", lambda: _async_return(None))

    result = await queue_module.enqueue_job("some_task", student_id="s1")

    assert result is None


@pytest.mark.asyncio
async def test_enqueue_job_returns_none_on_enqueue_error(monkeypatch):
    """A Redis outage must never fail the caller's request."""
    fake_pool = _FakePool(should_raise=True)
    monkeypatch.setattr(queue_module, "_get_pool", lambda: _async_return(fake_pool))

    result = await queue_module.enqueue_job("some_task", student_id="s1")

    assert result is None


@pytest.mark.asyncio
async def test_enqueue_job_returns_none_when_arq_returns_no_job(monkeypatch):
    """arq returns None for a duplicate job id rather than raising."""
    fake_pool = _FakePool(job_id=None)
    monkeypatch.setattr(queue_module, "_get_pool", lambda: _async_return(fake_pool))

    result = await queue_module.enqueue_job("some_task", student_id="s1")

    assert result is None


@pytest.mark.asyncio
async def test_falsy_on_failure_and_truthy_on_success(monkeypatch):
    """The contract callers rely on: `if not await enqueue_job(...)`.

    The return type changed from bool to the job id string, and these tests
    still asserted the old contract - so they had been failing on master
    unnoticed, because the project had no CI until now.
    """
    monkeypatch.setattr(queue_module, "_get_pool", lambda: _async_return(_FakePool()))
    assert bool(await queue_module.enqueue_job("t")) is True

    monkeypatch.setattr(queue_module, "_get_pool", lambda: _async_return(None))
    assert bool(await queue_module.enqueue_job("t")) is False


async def _async_return(value):
    return value
