"""Browser-bound login transactions. No credentials in browser session storage."""
import base64
import hashlib
import json
import re
import secrets
from urllib.parse import unquote, urlsplit, urlunsplit

TTL_SECONDS = 300
PREFIX = "samvaad:sso-transaction:"
COOKIE_PREFIX = "ss_login_"
CONSUME = """
local value = redis.call('GET', KEYS[1])
if not value or string.sub(value, 1, 64) ~= ARGV[1] then return nil end
redis.call('DEL', KEYS[1])
return string.sub(value, 66)
"""


def safe_destination(value: str | None) -> str:
    if not isinstance(value, str) or len(value) > 2048 or not value.startswith("/"):
        return "/"
    if re.search(r"%(?![0-9a-fA-F]{2})", value):
        return "/"
    decoded = unquote(value)
    if any(ord(c) <= 32 or ord(c) == 127 for c in decoded) or "\\" in decoded or decoded.startswith("//"):
        return "/"
    if re.search(r"%[0-9a-fA-F]{2}", decoded):
        return "/"
    try:
        parts = urlsplit(decoded)
        # Canonicalize dot segments for route-loop checks, without interpreting queries.
        segments = []
        for item in parts.path.split("/"):
            if item == "..":
                if segments:
                    segments.pop()
            elif item and item != ".":
                segments.append(item)
        if segments and segments[0].lower() in {"api", "auth", "login", "logout"}:
            return "/"
        if parts.netloc or parts.scheme:
            return "/"
        return value
    except ValueError:
        return "/"


def frontend_destination(frontend: str, destination: str) -> str:
    """Retain only the configured frontend origin, never a caller-controlled host."""
    parts = urlsplit(frontend)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
        raise ValueError("Invalid frontend origin")
    target = urlsplit(safe_destination(destination))
    return urlunsplit((parts.scheme, parts.netloc, target.path, target.query, target.fragment))


def cookie_name(state: str) -> str:
    if not isinstance(state, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", state):
        raise ValueError("Invalid login state")
    return COOKIE_PREFIX + state


async def create(client, destination: str | None, *, protocol="v1", product=None, callback=None):
    state = secrets.token_urlsafe(32)
    binding = secrets.token_urlsafe(32)
    payload = {"destination": safe_destination(destination)}
    if protocol == "v2":
        if not product or not callback:
            raise ValueError("Missing launch binding")
        payload.update(protocol="v2", product=product, callback=callback, verifier=secrets.token_urlsafe(48))
    elif protocol != "v1":
        raise ValueError("Unsupported launch protocol")
    value = hashlib.sha256(binding.encode()).hexdigest() + ":" + json.dumps(payload)
    stored = await client.set(PREFIX + state, value, ex=TTL_SECONDS, nx=True)
    if not stored:
        raise RuntimeError("Could not create login transaction")
    return state, binding


async def consume(client, state: str | None, binding: str | None):
    try:
        cookie_name(state)
    except (ValueError, TypeError):
        return None
    if not isinstance(binding, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", binding):
        return None
    raw = await client.eval(CONSUME, 1, PREFIX + state, hashlib.sha256(binding.encode()).hexdigest())
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("destination"), str):
            return None
        result = {"destination": safe_destination(payload["destination"])}
        if "protocol" in payload:
            if payload.get("protocol") != "v2" or not re.fullmatch(r"[A-Za-z0-9_-]{64}", payload.get("verifier", "")):
                return None
            if not all(isinstance(payload.get(key), str) and payload[key] for key in ("product", "callback")):
                return None
            result.update({key: payload[key] for key in ("protocol", "verifier", "product", "callback")})
        return result
    except (ValueError, TypeError):
        return None


async def challenge(client, state: str, binding: str) -> str:
    """Read only during issuance; do not expose the verifier to a browser."""
    raw = await client.get(PREFIX + state)
    if isinstance(raw, bytes):
        raw = raw.decode()
    if not raw or raw[:64] != hashlib.sha256(binding.encode()).hexdigest():
        raise ValueError("Missing launch transaction")
    verifier = json.loads(raw[65:])["verifier"]
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
