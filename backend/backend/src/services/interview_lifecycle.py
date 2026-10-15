import datetime


def interview_has_expired(
    interview,
    *,
    now: datetime.datetime | None = None,
) -> bool:
    """Return whether an interview's server-owned resume window has elapsed."""
    expires_at = getattr(interview, "expires_at", None)
    if expires_at is None:
        return False
    current = now or datetime.datetime.now(datetime.timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
    return expires_at <= current
