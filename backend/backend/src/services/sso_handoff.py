"""Short-lived compatibility handoff, not a replacement for server-held BFF sessions."""
import hashlib
import json
import re
import secrets
from src.services.sso_transactions import CONSUME, safe_destination

PREFIX = "samvaad:sso-handoff:"
COOKIE = "ss_handoff_"
TTL = 60


def valid(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is not None


async def issue(client, access, refresh, destination):
    if not all(isinstance(v, str) and v.strip() and len(v) <= 16384 for v in (access, refresh)):
        raise ValueError("Invalid handoff credentials")
    destination = safe_destination(destination)
    ticket, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    payload = {"token": access, "refresh_token": refresh, "return_to": destination}
    value = hashlib.sha256(binding.encode()).hexdigest() + ":" + json.dumps(payload)
    if not await client.set(PREFIX + ticket, value, ex=TTL, nx=True):
        raise RuntimeError("Could not create sign-in handoff")
    return ticket, binding


async def redeem(client, ticket, binding):
    if not valid(ticket) or not valid(binding):
        return None
    raw = await client.eval(CONSUME, 1, PREFIX + ticket, hashlib.sha256(binding.encode()).hexdigest())
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not all(isinstance(payload.get(k), str) and payload[k] for k in ("token", "refresh_token", "return_to")):
            return None
        if any(len(payload[k]) > 16384 or not payload[k].strip() for k in ("token", "refresh_token")):
            return None
        return {"token": payload["token"], "refresh_token": payload["refresh_token"], "return_to": safe_destination(payload["return_to"])}
    except (ValueError, TypeError):
        return None
