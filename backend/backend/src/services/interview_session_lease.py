import datetime
import hashlib
import hmac
import secrets

SESSION_LEASE_TTL = datetime.timedelta(seconds=90)


def new_session_lease_token() -> str:
    return secrets.token_urlsafe(32)


def session_lease_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_lease_matches(interview, token: str | None) -> bool:
    stored = getattr(interview, "session_lease_hash", None)
    if not stored or not token:
        return False
    return hmac.compare_digest(stored, session_lease_digest(token))


def session_lease_is_active(
    interview,
    *,
    now: datetime.datetime | None = None,
) -> bool:
    expires_at = getattr(interview, "session_lease_expires_at", None)
    if expires_at is None:
        return False
    current = now or datetime.datetime.now(datetime.timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    return expires_at > current
