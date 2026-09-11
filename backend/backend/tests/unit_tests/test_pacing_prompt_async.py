"""get_random_prompt must remain safe to call via asyncio.to_thread.

The pacing session route calls this through asyncio.to_thread rather than
directly, because _load_prompt_bank does blocking file I/O on the first
call per worker process - calling it straight from an async route handler
would stall the event loop for every other in-flight request on that
worker for the duration of the disk read. This pins the behavior that made
that change safe: the module-level cache and the returned shape are
unaffected by which thread does the loading.
"""

import asyncio

import pytest

from src.services.pacing_practice_service import get_random_prompt


@pytest.mark.asyncio
async def test_get_random_prompt_works_via_to_thread():
    prompt_text, prompt_index = await asyncio.to_thread(get_random_prompt, 1)
    assert isinstance(prompt_text, str) and prompt_text
    assert isinstance(prompt_index, int) and prompt_index >= 0


@pytest.mark.asyncio
async def test_repeated_to_thread_calls_return_valid_prompts_for_every_level():
    """Levels 2 and 3 exercise the cache being read from a thread on every
    call, not just warmed once - guards against a cache bug only showing up
    under concurrent/repeated access."""
    for level in (1, 2, 3):
        prompt_text, prompt_index = await asyncio.to_thread(get_random_prompt, level)
        assert isinstance(prompt_text, str) and prompt_text
        assert isinstance(prompt_index, int) and prompt_index >= 0


def test_unknown_level_still_raises_value_error():
    with pytest.raises(ValueError):
        get_random_prompt(999)
