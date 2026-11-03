"""A student chooses their own course and batch from what the program team has opened.

Sampark owns the list: an administrator creates a course and a batch there and switches the batch
"open for selection". Samvaad only reads that list and records what each student picked.
Nobody places a student on their behalf, and nothing here is guessed from typed text.

Choosing a batch sets the course on the learner (the same value staff course grants use) and
adds them to a group for that batch inside the course. Changing a choice ends the old row, so the
table is also the history. A student may belong to several courses but only one batch of each."""

import datetime as dt
import logging
import time

import fastapi
import httpx
import sqlalchemy as sa
from pydantic import BaseModel, Field, ValidationError

from src.services.barabari_integration import batches_connection
from src.models.db.cohort import Cohort, CohortMember
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.student_placement import StudentPlacement
from src.models.db.user import User
from src.services.people import student_only
from src.services.sampark_enrollment_sync import SELF_SOURCE

logger = logging.getLogger(__name__)
UTC = dt.timezone.utc
OPEN_BATCHES_PATH = "/samvaad-saathi/v1/open-batches"
CACHE_SECONDS = 120
MAX_CHOICES = 10
GROUP_PREFIX = "sampark:"
UNAVAILABLE = (
    "The list of courses could not be loaded right now. Your choice was not changed. "
    "Please try again in a minute."
)


class OpenBatch(BaseModel):
    """One choice, as Sampark sends it."""

    batchUniqueId: str | None = Field(default=None, max_length=128)
    batchId: str = Field(min_length=1, max_length=128)
    batchName: str = Field(min_length=1, max_length=200)
    courseId: int
    courseUniqueId: str | None = None
    courseName: str | None = Field(default=None, max_length=200)

    @property
    def key(self) -> str:
        return self.batchUniqueId or self.batchId

    @property
    def course(self) -> str:
        return str(self.courseId)

    def label(self) -> str:
        return f"{self.courseName or 'Course ' + self.course} · {self.batchName}"


_cache: dict = {"at": 0.0, "items": None}


def describe_failure(exc: Exception) -> str:
    """A one-line reason for a failed Sampark read, safe to log (never contains the key)."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        gateway = code == 401 and "authentication required" in exc.response.text.lower()
        hint = {
            401: (
                "the auth gateway refused it before Sampark saw it: /api/sampark/samvaad-saathi/** must be on the "
                "gateway's public list (deploy the latest Sampark auth-service) and the base URL must go through it"
                if gateway
                else "Sampark refused the service key (the key here must equal Sampark's SAMVAAD_SAATHI_AUTH_API_KEY)"
            ),
            403: "the service key was refused",
            404: "Sampark has no open-batches route (is Sampark's staging branch deployed, and is SAMPARK_SAATHI_BASE_URL its API base?)",
        }.get(code, "Sampark answered with an error (500 usually means its migration has not been run)" if code >= 500 else "unexpected answer")
        return f"HTTP {code} from {exc.request.url.host}: {hint}"
    if isinstance(exc, httpx.TimeoutException):
        return "Sampark did not answer in time"
    if isinstance(exc, httpx.HTTPError):
        return f"could not connect to Sampark ({type(exc).__name__})"
    return f"{type(exc).__name__}: {exc}"


def reset_cache() -> None:
    _cache.update(at=0.0, items=None)


async def open_batches(*, transport=None) -> tuple[list[OpenBatch], bool]:
    """The choices open right now, and whether they are the last good copy (Sampark was unreachable)."""
    now = time.monotonic()
    if _cache["items"] is not None and now - _cache["at"] < CACHE_SECONDS:
        return _cache["items"], False
    base, key = batches_connection()
    try:
        if not base or not key:
            missing = [name for name, value in (("base URL", base), ("service key", key)) if not value]
            raise RuntimeError(
                f"the Sampark {' and '.join(missing)} is not set (SAMPARK_BATCHES_BASE_URL / SAMPARK_BATCHES_API_KEY, "
                "or SAMPARK_SAATHI_BASE_URL / SAMPARK_SAATHI_API_KEY)"
            )
        async with httpx.AsyncClient(timeout=8.0, transport=transport) as client:
            response = await client.get(f"{base}{OPEN_BATCHES_PATH}", headers={"SAMVAAD-SAATHI-API-KEY": key})
        response.raise_for_status()
        body = response.json()
        rows = body.get("data") if isinstance(body, dict) and body.get("isSuccess") else None
        if not isinstance(rows, list) or len(rows) > 500:
            raise ValueError("Unexpected response from Sampark")
        items: list[OpenBatch] = []
        seen: set[str] = set()
        for row in rows:
            try:
                item = OpenBatch.model_validate(row)
            except ValidationError:
                continue  # a malformed row hides itself; it never breaks the list
            if item.key not in seen:
                seen.add(item.key)
                items.append(item)
    except Exception as exc:
        # Say why, without the key: a bare 503 told nobody whether the setting was missing, the
        # key was refused, the address was wrong or Sampark was down.
        logger.warning("Could not read Sampark's open batches: %s", describe_failure(exc))
        if _cache["items"] is not None:
            return _cache["items"], True
        raise fastapi.HTTPException(503, UNAVAILABLE)
    _cache.update(at=now, items=items)
    return items, False


def serialize(row: StudentPlacement) -> dict:
    return {
        "batch_key": row.batch_key,
        "batch_name": row.batch_name,
        "course_id": row.course_id,
        "course_name": row.course_name,
        "label": f"{row.course_name or 'Course ' + row.course_id} · {row.batch_name}",
        "selected_at": row.selected_at,
    }


async def require_student(session, user, *, lock: bool = False) -> User:
    """The learner row. Staff accounts have no course to choose, so they are refused."""
    stmt = sa.select(User).where(User.id == user.id, student_only()).execution_options(populate_existing=True)
    if lock:
        stmt = stmt.with_for_update()
    student = (await session.execute(stmt)).scalar_one_or_none()
    if student is None:
        raise fastapi.HTTPException(403, "Choosing a course is for learners.")
    return student


async def active_rows(session, student_id: int) -> list[StudentPlacement]:
    return list(
        (
            await session.execute(
                sa.select(StudentPlacement)
                .where(StudentPlacement.student_id == student_id, StudentPlacement.ended_at.is_(None))
                .order_by(StudentPlacement.selected_at, StudentPlacement.id)
            )
        )
        .scalars()
        .all()
    )


async def overview(session, user) -> dict:
    student = await require_student(session, user)
    rows = await active_rows(session, student.id)
    # Someone already given a course another way (an administrator) is placed; ask nothing more.
    placed = bool(rows) or bool(student.enrolled_course_ids)
    return {
        "needs_placement": not placed,
        "college": student.university,
        "selected": [serialize(r) for r in rows],
    }


async def group_for(session, batch: OpenBatch | StudentPlacement, course_id: str, name: str) -> Cohort:
    """The group for one batch inside one course, made the first time anyone picks it."""
    key = batch.key if isinstance(batch, OpenBatch) else batch.batch_key
    request_key = f"{GROUP_PREFIX}{key}"[:64]
    found = (
        await session.execute(
            sa.select(Cohort).where(Cohort.course_id == course_id, Cohort.request_key == request_key)
        )
    ).scalar_one_or_none()
    if found is not None:
        return found
    cohort = Cohort(course_id=course_id, name=name[:120], request_key=request_key)
    session.add(cohort)
    await session.flush()
    return cohort


async def replace(session, user, batch_keys: list[str], *, transport=None) -> dict:
    keys = list(dict.fromkeys(k.strip() for k in batch_keys if isinstance(k, str) and k.strip()))
    if not keys:
        raise fastapi.HTTPException(422, "Choose at least one course and batch.")
    if len(keys) > MAX_CHOICES:
        raise fastapi.HTTPException(422, f"Choose at most {MAX_CHOICES} courses.")

    student = await require_student(session, user, lock=True)
    if student.enrollment_source == "sampark" and student.enrolled_course_ids:
        raise fastapi.HTTPException(
            409, "Your courses were set by your program team. Ask them to change them."
        )
    current = {r.batch_key: r for r in await active_rows(session, student.id)}
    new_keys = [k for k in keys if k not in current]
    options: dict[str, OpenBatch] = {}
    if new_keys:
        listed, _stale = await open_batches(transport=transport)
        options = {o.key: o for o in listed}
        closed = [k for k in new_keys if k not in options]
        if closed:
            raise fastapi.HTTPException(422, "One of those courses is not open for choosing. Refresh and try again.")

    chosen: dict[str, tuple[str, str, str | None]] = {}  # key -> (course_id, batch_name, course_name)
    for k in keys:
        if k in current:
            r = current[k]
            chosen[k] = (r.course_id, r.batch_name, r.course_name)
        else:
            o = options[k]
            chosen[k] = (o.course, o.batchName, o.courseName)
    courses = [c for c, _, _ in chosen.values()]
    if len(set(courses)) != len(courses):
        raise fastapi.HTTPException(422, "Choose only one batch for each course.")

    now = dt.datetime.now(UTC)
    removed = [r for k, r in current.items() if k not in keys]
    added: list[StudentPlacement] = []
    for r in removed:
        r.ended_at = now
        group = (
            await session.execute(
                sa.select(Cohort.id).where(
                    Cohort.course_id == r.course_id, Cohort.request_key == f"{GROUP_PREFIX}{r.batch_key}"[:64]
                )
            )
        ).scalar_one_or_none()
        if group is not None:
            await session.execute(
                sa.delete(CohortMember).where(CohortMember.cohort_id == group, CohortMember.student_id == student.id)
            )
    await session.flush()  # end the old rows before replacements go in, so the unique index never sees both
    for k in new_keys:
        course_id, batch_name, course_name = chosen[k]
        row = StudentPlacement(
            student_id=student.id, batch_key=k, batch_name=batch_name, course_id=course_id, course_name=course_name
        )
        session.add(row)
        added.append(row)
        group = await group_for(session, options[k], course_id, batch_name)
        exists = (
            await session.execute(
                sa.select(CohortMember.id).where(CohortMember.cohort_id == group.id, CohortMember.student_id == student.id)
            )
        ).first()
        if exists is None:
            session.add(CohortMember(cohort_id=group.id, student_id=student.id))
    await session.flush()

    # Only the courses chosen here are added or removed; a course an administrator gave this
    # learner another way is left exactly as it was.
    ids = list(student.enrolled_course_ids or [])
    for r in removed:
        if r.course_id in ids:
            ids.remove(r.course_id)
    for r in added:
        if r.course_id not in ids:
            ids.append(r.course_id)
    student.enrolled_course_ids = sorted(ids)
    if student.enrollment_source is None:
        student.enrollment_source = SELF_SOURCE
    student.enrollment_observed_at = now

    if removed or added:
        session.add(
            PracticeDashboardAuditEvent(
                actor_id=student.id,
                action="student_placement_changed",
                purpose="placement",
                target_type="student",
                target_id=str(student.id),
                reason="Chosen by the student.",
                event_metadata={
                    "added": [{"batch": r.batch_key, "course_id": r.course_id} for r in added],
                    "removed": [{"batch": r.batch_key, "course_id": r.course_id} for r in removed],
                },
            )
        )
    await session.commit()
    for row in added:
        await tell_sampark(student.email, row.batch_key)
    rows = await active_rows(session, student.id)
    return {"needs_placement": False, "college": student.university, "selected": [serialize(r) for r in rows]}


async def tell_sampark(email: str, batch_key: str) -> None:
    """Best effort, after the choice is saved: Sampark's records follow the learner's choice.
    If the queue is down the choice still stands and Sampark simply is not told this time."""
    from uuid import uuid4

    from src.worker.queue import enqueue_job

    try:
        queued = await enqueue_job(
            "submit_batch_choice_task", email=email, batch_key=batch_key, request_id=str(uuid4())
        )
    except Exception:  # never fail a saved choice
        queued = None
    if not queued:
        logger.warning("Could not queue the Sampark batch-choice callback for batch %s", batch_key)
