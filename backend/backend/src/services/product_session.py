"""Server-only product session store. Not yet wired into HTTP authentication.

Callers must validate central identity before creation and after token refresh.
Redis failures propagate: never fall back to a browser bearer credential.
"""
import hashlib
import json
import re
import secrets
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

PREFIX = "samvaad:product-session:"
MAX_TTL = 8 * 60 * 60
LOCK_MS = 20_000

# Atomic refresh reservation returns only one owner the current encrypted record.
RESERVE = """
if not redis.call('GET', KEYS[1]) then return nil end
if not redis.call('SET', KEYS[2], ARGV[1], 'NX', 'PX', ARGV[2]) then return nil end
return redis.call('GET', KEYS[1])
"""
# Never recreate a revoked/expired session, overwrite a newer refresh, or extend TTL.
COMMIT = """
if redis.call('GET', KEYS[2]) ~= ARGV[1] then return 0 end
local ttl = redis.call('PTTL', KEYS[1])
if ttl <= 0 or redis.call('GET', KEYS[1]) ~= ARGV[2] then
 redis.call('DEL', KEYS[2]); return 0
end
redis.call('SET', KEYS[1], ARGV[3], 'PX', ttl)
redis.call('DEL', KEYS[2], KEYS[3])
return 1
"""
RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
 return redis.call('DEL', KEYS[1])
end
return 0
"""
REVOKE = "return redis.call('DEL', KEYS[1], KEYS[2], KEYS[3])"
REVOKE_SNAPSHOT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1], KEYS[2], KEYS[3])
return 1
"""
BEGIN_REFRESH = """
if redis.call('GET', KEYS[2]) ~= ARGV[1] or redis.call('GET', KEYS[1]) ~= ARGV[2] then return 0 end
local ttl = redis.call('PTTL', KEYS[1])
if ttl <= 0 then return 0 end
if not redis.call('SET', KEYS[3], 'dispatched', 'NX', 'PX', ttl) then return 0 end
return 1
"""


@dataclass(frozen=True, repr=False)
class Session:
    principal: str
    access_token: str
    refresh_token: str
    csrf: str
    generation: int = 0


@dataclass(frozen=True, repr=False)
class RefreshLease:
    session_id: str
    owner: str
    encrypted: bytes
    session: Session


class ProductSessions:
    def __init__(self, client, encryption_keys):
        # Explicit key provisioning; no ephemeral key or JWT-secret fallback.
        if not isinstance(encryption_keys, (list, tuple)) or not 1 <= len(encryption_keys) <= 3 or len(set(encryption_keys)) != len(encryption_keys):
            raise ValueError("Product session encryption keys required")
        self.cipher = MultiFernet([Fernet(key) for key in encryption_keys])
        self.client = client

    @staticmethod
    def key(session_id):
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", session_id):
            return None
        # Redis keys themselves must not be usable as browser credentials.
        return PREFIX + hashlib.sha256(session_id.encode()).hexdigest()

    def encode(self, session):
        return self.cipher.encrypt(json.dumps(vars(session)).encode())

    def decode(self, raw):
        if not isinstance(raw, (bytes, str)) or not raw or len(raw) > 100_000:
            return None
        try:
            data = json.loads(self.cipher.decrypt(raw.encode() if isinstance(raw, str) else raw))
            if not isinstance(data, dict) or set(data) != {"principal", "access_token", "refresh_token", "csrf", "generation"}:
                return None
            if not all(isinstance(data.get(k), str) and data[k].strip() for k in
                       ("principal", "access_token", "refresh_token", "csrf")):
                return None
            if type(data.get("generation")) is not int or data["generation"] < 0:
                return None
            if len(data["principal"]) > 128 or data["principal"] != data["principal"].strip():
                return None
            if any(len(data[k]) > 16384 for k in ("access_token", "refresh_token")) or len(data["csrf"]) > 128:
                return None
            return Session(**data)
        except (InvalidToken, ValueError, TypeError):
            return None

    async def create(self, principal, access_token, refresh_token, *, ttl=MAX_TTL):
        if type(ttl) is not int or not 1 <= ttl <= MAX_TTL:
            raise ValueError("Invalid session lifetime")
        if not all(isinstance(v, str) and v.strip() for v in (principal, access_token, refresh_token)):
            raise ValueError("Verified identity and token pair required")
        if len(principal) > 128 or principal != principal.strip() or any(len(v) > 16384 for v in (access_token, refresh_token)):
            raise ValueError("Session field exceeds limits")
        session_id = secrets.token_urlsafe(32)
        session = Session(principal, access_token, refresh_token, secrets.token_urlsafe(32))
        if not await self.client.set(self.key(session_id), self.encode(session), nx=True, ex=ttl):
            raise RuntimeError("Could not allocate session")
        return session_id, session

    async def read(self, session_id):
        key = self.key(session_id)
        if not key:
            return None
        return self.decode(await self.client.get(key))

    async def revoke(self, session_id):
        key = self.key(session_id)
        if key:
            await self.client.eval(REVOKE, 3, key, key + ":refresh", key + ":dispatched")

    async def reserve_refresh(self, session_id):
        key = self.key(session_id)
        if not key:
            return None
        owner = secrets.token_urlsafe(32)
        raw = await self.client.eval(RESERVE, 2, key, key + ":refresh", owner, LOCK_MS)
        session = self.decode(raw)
        if session is None:
            if raw:
                await self.client.eval(RELEASE, 1, key + ":refresh", owner)
            return None
        return RefreshLease(session_id, owner, raw, session)

    async def finish_refresh(self, lease, principal, access_token, refresh_token):
        # The caller derives principal from a verified refreshed token, never form input.
        if principal != lease.session.principal:
            await self.revoke_refresh_snapshot(lease)
            raise ValueError("Refresh identity changed")
        if not all(isinstance(v, str) and v.strip() and len(v) <= 16384 for v in (access_token, refresh_token)):
            raise ValueError("Invalid refresh response")
        key = self.key(lease.session_id)
        updated = Session(principal, access_token, refresh_token, lease.session.csrf, lease.session.generation + 1)
        return bool(await self.client.eval(COMMIT, 3, key, key + ":refresh", key + ":dispatched",
                                           lease.owner, lease.encrypted, self.encode(updated)))

    async def release_refresh(self, lease):
        key = self.key(lease.session_id)
        return bool(await self.client.eval(RELEASE, 1, key + ":refresh", lease.owner))

    async def begin_refresh(self, lease):
        """Durable send marker: process death must not permit refresh-token reuse."""
        key = self.key(lease.session_id)
        return bool(await self.client.eval(BEGIN_REFRESH, 3, key, key + ":refresh", key + ":dispatched",
                                           lease.owner, lease.encrypted))

    async def revoke_refresh_snapshot(self, lease):
        """Revoke only the exact record this worker observed, never a newer refresh."""
        key = self.key(lease.session_id)
        return bool(await self.client.eval(REVOKE_SNAPSHOT, 3, key, key + ":refresh", key + ":dispatched", lease.encrypted))
