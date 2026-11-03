"""Complete per-student snapshots from the approved Sampark source. Disabled by default."""

import datetime
from urllib.parse import urlsplit
import httpx
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator
from src.config.manager import settings
from src.models.db.user import User

UTC = datetime.timezone.utc


def enabled():
    return bool(settings.SAMPARK_ENROLLMENT_SYNC_ENABLED)


def max_age():
    age = settings.SAMPARK_ENROLLMENT_MAX_AGE_SECONDS
    if type(age) is not int or age < 300 or age > 86400:
        raise ValueError("Enrollment max age must be 300–86400 seconds")
    return age


def fresh(user, now=None):
    if not enabled():
        return True
    observed = getattr(user, "enrollment_observed_at", None)
    now = now or datetime.datetime.now(UTC)
    return (
        getattr(user, "enrollment_source", None) == "sampark"
        and observed is not None
        and observed.tzinfo is not None
        and now - datetime.timedelta(seconds=max_age())
        <= observed
        <= now + datetime.timedelta(seconds=300)
    )


def freshness_predicate():
    if not enabled():
        return sa.true()
    return sa.and_(
        User.enrollment_source == "sampark",
        User.enrollment_observed_at
        >= datetime.datetime.now(UTC) - datetime.timedelta(seconds=max_age()),
        User.enrollment_observed_at
        <= datetime.datetime.now(UTC) + datetime.timedelta(seconds=300),
    )


class Snapshot(BaseModel):
    studentId: str = Field(pattern=r"^[0-9]+$")
    studentUniqueId: str | None = Field(default=None, max_length=128)
    email: str = Field(min_length=3, max_length=254)
    courseIds: list[str] = Field(max_length=100)

    @field_validator("courseIds")
    @classmethod
    def course_ids(cls, value):
        if any(not x or len(x) > 128 or x.strip() != x or x == "*" for x in value):
            raise ValueError("Explicit course IDs required")
        return sorted(set(value))


class Page(BaseModel):
    source: str
    observedAt: datetime.datetime
    items: list[Snapshot] = Field(max_length=200)
    nextCursor: str | None = Field(default=None, pattern=r"^[0-9]+$")


async def apply_page(session, page: Page):
    now = datetime.datetime.now(UTC)
    if (
        page.source != "sampark"
        or page.observedAt.tzinfo is None
        or not now - datetime.timedelta(seconds=max_age())
        <= page.observedAt
        <= now + datetime.timedelta(seconds=300)
    ):
        raise ValueError("Invalid, stale or future Sampark snapshot")
    ids = [item.studentUniqueId for item in page.items if item.studentUniqueId]
    emails = [item.email.strip().casefold() for item in page.items]
    if len(ids) != len(set(ids)) or len(emails) != len(set(emails)):
        raise ValueError("Ambiguous Sampark identities")
    rows = (
        (
            await session.execute(
                sa.select(User)
                .where(
                    sa.or_(
                        User.sampark_student_unique_id.in_(ids),
                        sa.func.lower(User.email).in_(emails),
                    ),
                    User.role.is_(None),
                    User.is_admin.is_(False),
                    User.is_super_admin.is_(False),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    by_id = {
        u.sampark_student_unique_id: u for u in rows if u.sampark_student_unique_id
    }
    by_email = {u.email.casefold(): u for u in rows}
    count = 0
    for item in page.items:
        if not item.studentUniqueId:
            continue  # unregistered identity must not receive course access
        user = by_id.get(item.studentUniqueId) or by_email.get(
            item.email.strip().casefold()
        )
        if user is None:
            continue  # don't create accounts from a roster feed
        email_match = by_email.get(item.email.strip().casefold())
        if email_match is not None and email_match is not user:
            raise ValueError(
                "Sampark email and stable identity resolve to different users"
            )
        if user.sampark_student_unique_id not in (None, item.studentUniqueId):
            raise ValueError("Sampark identity binding conflict")
        if (
            user.enrollment_observed_at is not None
            and user.enrollment_observed_at >= page.observedAt
        ):
            continue
        user.sampark_student_unique_id = item.studentUniqueId
        user.enrolled_course_ids = item.courseIds
        user.enrollment_observed_at = page.observedAt
        user.enrollment_source = "sampark"
        count += 1
    return count


async def synchronize(session, *, transport=None):
    if not enabled():
        return {"status": "disabled"}
    url = settings.SAMPARK_ENROLLMENT_SYNC_URL
    parsed = urlsplit(url)
    if (
        not settings.SAMPARK_ENROLLMENT_SYNC_KEY
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not (
            parsed.scheme == "https"
            or parsed.scheme == "http"
            and parsed.hostname in ("127.0.0.1", "localhost")
        )
    ):
        raise ValueError(
            "Configure the dedicated Sampark key and an HTTPS snapshot endpoint"
        )
    # Transaction-scoped lock: one writer for a complete pull, automatically released on failure.
    locked = (
        await session.execute(sa.text("SELECT pg_try_advisory_xact_lock(19670821)"))
    ).scalar_one()
    if not locked:
        return {"status": "already_running"}
    cursor = "0"
    seen = set()
    emails = set()
    updated = 0
    started = datetime.datetime.now(UTC)
    async with httpx.AsyncClient(
        transport=transport, timeout=20, follow_redirects=False
    ) as client:
        for _page in range(1000):
            response = await client.get(
                url,
                params={"after": cursor, "limit": 200},
                headers={
                    "X-Sampark-Enrollment-Key": settings.SAMPARK_ENROLLMENT_SYNC_KEY
                },
            )
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise ValueError("Oversized Sampark snapshot")
            page = Page.model_validate(response.json())
            for item in page.items:
                if (
                    item.studentUniqueId
                    and item.studentUniqueId in seen
                    or item.email.casefold() in emails
                ):
                    raise ValueError("Repeated identity in Sampark pagination")
                if item.studentUniqueId:
                    seen.add(item.studentUniqueId)
                emails.add(item.email.casefold())
            updated += await apply_page(session, page)
            if page.nextCursor is None:
                break
            if int(page.nextCursor) <= int(cursor):
                raise ValueError("Sampark pagination did not advance")
            cursor = page.nextCursor
        else:
            raise ValueError("Sampark pagination exceeded safety limit")
    # Reconcile hard deletions only after a complete successful pull. Partial feeds never revoke everyone.
    await session.execute(
        sa.update(User)
        .where(
            sa.or_(
                User.enrollment_observed_at.is_(None),
                User.enrollment_observed_at <= started,
            ),
            User.sampark_student_unique_id.is_not(None),
            ~User.sampark_student_unique_id.in_(seen),
            User.role.is_(None),
            User.is_admin.is_(False),
            User.is_super_admin.is_(False),
        )
        .values(
            enrolled_course_ids=[],
            enrollment_observed_at=started,
            enrollment_source="sampark",
        )
    )
    await session.commit()
    return {"status": "complete", "updated": updated, "source_students": len(seen)}
