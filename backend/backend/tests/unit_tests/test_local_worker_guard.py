from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import importlib

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('queued, expected', [(None, False), (0.0, True), (123.0, True)])
async def test_local_worker_guard_distinguishes_completed_cron_markers(monkeypatch, queued, expected):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[4] / 'ops/local-test'))
    runtime = importlib.import_module('runtime_acceptance')
    async def keys(pattern):
        assert pattern == 'arq:in-progress:*'
        yield b'arq:in-progress:cron:test'
    redis = SimpleNamespace(scan_iter=keys, zscore=AsyncMock(return_value=queued))
    assert await runtime.worker_has_active_jobs(redis) is expected
    redis.zscore.assert_awaited_once_with('arq:queue', b'cron:test')
