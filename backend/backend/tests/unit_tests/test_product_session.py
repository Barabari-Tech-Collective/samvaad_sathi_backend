import asyncio
import os
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from src.services.product_session import ProductSessions, Session, MAX_TTL


@pytest.fixture
def store():
    return ProductSessions(AsyncMock(), [Fernet.generate_key()])


@pytest.mark.parametrize("value", [None, "", "x", "x" * 44, "../session", 42])
@pytest.mark.asyncio
async def test_invalid_identifier_never_reads_redis(store, value):
    assert await store.read(value) is None
    assert await store.reserve_refresh(value) is None
    await store.revoke(value)
    store.client.get.assert_not_called()
    store.client.eval.assert_not_called()


@pytest.mark.parametrize("ttl", [0, -1, MAX_TTL + 1, True, "60"])
@pytest.mark.asyncio
async def test_invalid_lifetime_never_creates(store, ttl):
    with pytest.raises(ValueError):
        await store.create("u", "a", "r", ttl=ttl)
    store.client.set.assert_not_called()


@pytest.mark.parametrize("tokens", [("", "a", "r"), ("u", "", "r"), ("u", "a", None)])
@pytest.mark.asyncio
async def test_missing_identity_or_tokens_rejected(store, tokens):
    with pytest.raises(ValueError):
        await store.create(*tokens)
    store.client.set.assert_not_called()


def test_explicit_encryption_key_required():
    with pytest.raises(ValueError):
        ProductSessions(AsyncMock(), [])


def test_ciphertext_and_repr_do_not_expose_credentials(store):
    session = Session("user", "sensitive-access", "sensitive-refresh", "csrf")
    encrypted = store.encode(session)
    assert b"sensitive" not in encrypted
    assert "sensitive" not in repr(session)
    assert store.decode(encrypted) == session
    # Mutate authenticated bytes, not a base64 character that may already be x
    # (or differ only in unused padding bits).
    import base64
    damaged = bytearray(base64.urlsafe_b64decode(encrypted))
    damaged[-1] ^= 1
    assert store.decode(base64.urlsafe_b64encode(damaged)) is None
    assert ProductSessions(AsyncMock(), [Fernet.generate_key()]).decode(encrypted) is None


def test_rotating_encryption_key_can_read_old_sessions():
    old, new = Fernet.generate_key(), Fernet.generate_key()
    session = Session("u", "a", "r", "c")
    previous = ProductSessions(AsyncMock(), [old])
    rotated = ProductSessions(AsyncMock(), [new, old])
    assert rotated.decode(previous.encode(session)) == session
    assert previous.decode(rotated.encode(session)) is None


@pytest.mark.asyncio
async def test_redis_outage_propagates_without_fallback(store):
    store.client.get.side_effect = ConnectionError("offline")
    with pytest.raises(ConnectionError):
        await store.read("x" * 43)


@pytest.mark.asyncio
async def test_collision_does_not_overwrite(store):
    store.client.set.return_value = False
    with pytest.raises(RuntimeError):
        await store.create("u", "a", "r")
    assert store.client.set.call_args.kwargs["nx"] is True


@pytest.fixture
async def live():
    if os.getenv("RUN_LOCAL_AUTH_REDIS") != "1":
        pytest.skip("Explicit isolated Redis acceptance only")
    import redis.asyncio as redis
    client = redis.Redis(host="127.0.0.1", port=26379, db=15, socket_connect_timeout=2, socket_timeout=2)
    service = ProductSessions(client, [Fernet.generate_key()])
    created = []
    async def make(**kwargs):
        sid, session = await service.create("user-1", "synthetic-access", "synthetic-refresh", **kwargs)
        created.append(sid)
        return sid, session
    try:
        yield service, make
    finally:
        for sid in created:
            await service.revoke(sid)
        await client.aclose()


@pytest.mark.asyncio
async def test_live_storage_hides_tokens_and_cookie_and_has_ttl(live):
    store, make = live
    sid, session = await make()
    key = store.key(sid)
    assert sid not in key
    assert b"synthetic" not in await store.client.get(key)
    assert 0 < await store.client.ttl(key) <= MAX_TTL
    assert await store.read(sid) == session


@pytest.mark.asyncio
async def test_live_only_one_of_twenty_refreshers_reserves(live):
    store, make = live
    sid, _ = await make()
    leases = await asyncio.gather(*(store.reserve_refresh(sid) for _ in range(20)))
    assert sum(x is not None for x in leases) == 1


@pytest.mark.asyncio
async def test_live_refresh_preserves_absolute_expiry_and_csrf(live):
    store, make = live
    sid, original = await make(ttl=60)
    before = await store.client.pttl(store.key(sid))
    lease = await store.reserve_refresh(sid)
    assert await store.finish_refresh(lease, "user-1", "new-access", "new-refresh")
    after = await store.client.pttl(store.key(sid))
    assert 0 < after <= before
    current = await store.read(sid)
    assert current.csrf == original.csrf and current.generation == 1
    assert current.access_token == "new-access"
    assert not await store.finish_refresh(lease, "user-1", "stale", "stale")


@pytest.mark.asyncio
async def test_live_logout_wins_over_inflight_refresh(live):
    store, make = live
    sid, _ = await make()
    lease = await store.reserve_refresh(sid)
    await store.revoke(sid)
    await store.revoke(sid)
    assert not await store.finish_refresh(lease, "user-1", "new", "new")
    assert await store.read(sid) is None
    assert await store.reserve_refresh(sid) is None


@pytest.mark.asyncio
async def test_live_expired_session_cannot_be_resurrected(live):
    store, make = live
    sid, _ = await make()
    lease = await store.reserve_refresh(sid)
    await store.client.pexpire(store.key(sid), 1)
    await asyncio.sleep(.02)
    assert not await store.finish_refresh(lease, "user-1", "new", "new")
    assert await store.read(sid) is None


@pytest.mark.asyncio
async def test_live_stale_lease_cannot_commit_or_unlock_new_owner(live):
    store, make = live
    sid, _ = await make()
    old = await store.reserve_refresh(sid)
    await store.client.pexpire(store.key(sid) + ":refresh", 1)
    await asyncio.sleep(.02)
    new = await store.reserve_refresh(sid)
    assert new is not None
    assert not await store.release_refresh(old)
    assert not await store.finish_refresh(old, "user-1", "stale", "stale")
    assert await store.finish_refresh(new, "user-1", "new", "new")


@pytest.mark.asyncio
async def test_live_account_change_in_refresh_revokes_session(live):
    store, make = live
    sid, _ = await make()
    lease = await store.reserve_refresh(sid)
    with pytest.raises(ValueError):
        await store.finish_refresh(lease, "different-user", "new", "new")
    assert await store.read(sid) is None


@pytest.mark.asyncio
async def test_live_failed_provider_response_cannot_overwrite(live):
    store, make = live
    sid, original = await make()
    lease = await store.reserve_refresh(sid)
    with pytest.raises(ValueError):
        await store.finish_refresh(lease, "user-1", "", "new")
    assert await store.release_refresh(lease)
    assert await store.read(sid) == original
    assert await store.reserve_refresh(sid) is not None


@pytest.mark.asyncio
async def test_live_independent_sessions_are_isolated(live):
    store, make = live
    a, _ = await make()
    b, second = await make()
    assert a != b
    await store.revoke(a)
    assert await store.read(b) == second


@pytest.mark.asyncio
async def test_live_corrupt_record_fails_closed_without_lock_leak(live):
    store, make = live
    sid, _ = await make()
    await store.client.set(store.key(sid), b"corrupt", ex=60)
    assert await store.read(sid) is None
    assert await store.reserve_refresh(sid) is None
    assert not await store.client.exists(store.key(sid) + ":refresh")


@pytest.mark.parametrize("payload", [b"[]", b"null", b'{"principal":"u"}', b'{"principal":"u","access_token":"a","refresh_token":"r","csrf":"c","generation":true}'])
def test_authenticated_but_malformed_payload_fails_closed(store, payload):
    assert store.decode(store.cipher.encrypt(payload)) is None


@pytest.mark.asyncio
async def test_live_changed_record_cannot_be_overwritten_by_old_snapshot(live):
    store, make = live
    sid, original = await make()
    lease = await store.reserve_refresh(sid)
    changed = Session(original.principal, "changed", "changed", original.csrf, 5)
    await store.client.set(store.key(sid), store.encode(changed), ex=60)
    assert not await store.finish_refresh(lease, "user-1", "stale", "stale")
    assert await store.read(sid) == changed


@pytest.mark.asyncio
async def test_live_crash_after_dispatch_prevents_refresh_token_reuse(live):
    store,make=live
    sid,_=await make()
    first=await store.reserve_refresh(sid)
    assert await store.begin_refresh(first)
    await store.client.pexpire(store.key(sid)+':refresh',1)
    await asyncio.sleep(.02)
    second=await store.reserve_refresh(sid)
    assert second is not None
    assert not await store.begin_refresh(second)
    assert not await store.finish_refresh(first,'user-1','late','late')


@pytest.mark.asyncio
async def test_live_successful_commit_clears_dispatch_marker(live):
    store,make=live
    sid,_=await make()
    first=await store.reserve_refresh(sid)
    assert await store.begin_refresh(first)
    assert await store.finish_refresh(first,'user-1','new','new')
    second=await store.reserve_refresh(sid)
    assert await store.begin_refresh(second)


@pytest.mark.asyncio
async def test_live_stale_refresh_failure_cannot_revoke_new_session_record(live):
    store, make = live
    sid, _ = await make()
    old = await store.reserve_refresh(sid)
    await store.client.pexpire(store.key(sid) + ':refresh', 1)
    await asyncio.sleep(.02)
    new = await store.reserve_refresh(sid)
    assert await store.begin_refresh(new)
    assert await store.finish_refresh(new, 'user-1', 'new-access', 'new-refresh')
    assert not await store.revoke_refresh_snapshot(old)
    assert (await store.read(sid)).access_token == 'new-access'
    with pytest.raises(ValueError):
        await store.finish_refresh(old, 'different-user', 'late', 'late')
    assert (await store.read(sid)).access_token == 'new-access'


@pytest.mark.asyncio
async def test_live_ambiguous_refresh_cleanup_removes_unchanged_record_and_marker(live):
    store, make = live
    sid, _ = await make()
    lease = await store.reserve_refresh(sid)
    assert await store.begin_refresh(lease)
    assert await store.revoke_refresh_snapshot(lease)
    assert await store.read(sid) is None
    assert not await store.client.exists(store.key(sid) + ':dispatched')
    assert not await store.client.exists(store.key(sid) + ':refresh')
    assert not await store.revoke_refresh_snapshot(lease)


@pytest.mark.asyncio
async def test_live_one_hundred_independent_session_lifecycles(live):
    """Concurrency simulation against isolated Redis; not production load capacity."""
    store, _ = live
    created = []
    async def journey(index):
        principal = f"synthetic-user-{index}"
        sid, session = await store.create(principal, f"access-{index}", f"refresh-{index}")
        created.append(sid)
        reads = await asyncio.gather(*(store.read(sid) for _ in range(5)))
        assert all(row.principal == principal and row.csrf == session.csrf for row in reads)
        leases = await asyncio.gather(*(store.reserve_refresh(sid) for _ in range(5)))
        winners = [lease for lease in leases if lease is not None]
        assert len(winners) == 1
        lease = winners[0]
        assert await store.begin_refresh(lease)
        assert await store.finish_refresh(lease, principal, f"rotated-access-{index}", f"rotated-refresh-{index}")
        refreshed = await store.read(sid)
        assert refreshed.principal == principal and refreshed.csrf == session.csrf
        await store.revoke(sid)
        assert await store.read(sid) is None
    try:
        await asyncio.gather(*(journey(index) for index in range(100)))
        assert len(set(created)) == 100
    finally:
        await asyncio.gather(*(store.revoke(sid) for sid in created))
