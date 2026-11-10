"""The staff Learners area: figures, queues, lists, exports and bulk actions (one scope)."""

import csv
import datetime as dt
import io
from typing import Literal
from uuid import UUID

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator, model_validator

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes import cohorts as cohort_routes
from src.models.db.college_alias import CollegeAlias, LearnerImportCollege
from src.models.db.notification import Notification, NotificationDelivery
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.interview import Interview
from src.models.db.user import User
from src.services import learner_hub as hub, notifications

router = fastapi.APIRouter(prefix="/v2/learner-hub", tags=["learner-hub"])
MAX_ACTION = 2000
MAX_EXPORT = 5000
NUDGE_GAP_DAYS = 3


def level_of(college, course, batch):
    return hub.Level(college or None, course or None, batch or None)


def level_out(context, level):
    label = None
    if level.college:
        label = next((p.college_label for p in context.learners if p.college_key == level.college), level.college)
    course_name = (
        ("Not in a course" if level.course == hub.NO_COURSE else context.course_names.get(level.course, level.course))
        if level.course
        else None
    )
    batch_name = None
    if level.batch:
        batch_name = "No batch chosen" if level.batch == hub.NO_BATCH else next(
            (b[2] for p in context.learners for b in p.batches if b[1] == level.batch and (not level.course or b[0] == level.course)),
            level.batch,
        )
    return {
        "college": {"key": level.college, "label": label} if level.college else None,
        "course": {"id": level.course, "name": course_name} if level.course else None,
        "batch": {"key": level.batch, "name": batch_name} if level.batch else None,
    }


@router.get("/summary")
async def summary(
    response: fastapi.Response,
    days: int = 7,
    college: str = "",
    course: str = "",
    batch: str = "",
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    context = await hub.load_context(user, session, days, course or None)
    level = hub.effective_level(context, level_of(college, course, batch))
    people = hub.filter_learners(context, level)
    ids = {p.id for p in people}
    period = hub.period_figures(context, ids)
    colleges = {p.college_key for p in people if p.college_key != hub.NOT_SET}
    response.headers["Cache-Control"] = "no-store"
    return {
        "days": days,
        "start": context.start.isoformat(),
        "end": context.today.isoformat(),
        "lens": context.viewer.lens,
        "can": hub.capabilities(context.viewer),
        "level": level_out(context, level),
        "root": hub.effective_level(context, hub.Level()) == level,
        "learners": len(people),
        "colleges": len(colleges),
        "metrics": period["metrics"],
        "outcomes": hub.outcomes(context, people, period["current"]),
        "actions": hub.actions_for(context, people),
        "funnel": hub.funnel(people),
        "weak_skills": await hub.weak_skills(session, people) if level.college and not level.batch else None,
    }


@router.get("/groups")
async def groups(
    response: fastapi.Response,
    of: Literal["colleges", "courses", "batches"] = "colleges",
    days: int = 7,
    college: str = "",
    course: str = "",
    batch: str = "",
    q: str = fastapi.Query("", max_length=120),
    page: int = fastapi.Query(1, ge=1),
    limit: int = fastapi.Query(25, ge=1, le=100),
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    context = await hub.load_context(user, session, days, course or None)
    level = hub.effective_level(context, level_of(college, course, batch))
    rows, no_batch = hub.group_rows(context, of, level, q)
    response.headers["Cache-Control"] = "no-store"
    return {
        "lens": context.viewer.lens,
        "items": rows[(page - 1) * limit : page * limit],
        "total": len(rows),
        "page": page,
        "limit": limit,
        "no_batch": no_batch,
        "can": hub.capabilities(context.viewer),
    }


@router.get("/learners")
async def learners(
    response: fastapi.Response,
    days: int = 7,
    college: str = "",
    course: str = "",
    batch: str = "",
    filter_college: str = "",
    filter_course: str = "",
    filter_batch: str = "",
    status: str = "all",
    sort: str = "urgent",
    q: str = fastapi.Query("", max_length=120),
    page: int = fastapi.Query(1, ge=1),
    limit: int = fastapi.Query(25, ge=1, le=100),
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    context_course = course or filter_course or None
    context = await hub.load_context(user, session, days, context_course)
    level = hub.effective_level(context, level_of(college, course, batch))
    filters = {"college": filter_college, "course": filter_course, "batch": filter_batch}
    found = hub.match_learners(context, level, q=q, status=status, **filters)
    ordered = hub.sort_learners(context, found, sort)
    everyone = hub.match_learners(context, level, q=q, status="all", **filters)

    def options(kind):
        pool = hub.match_learners(context, level, q=q, status="all", **filters, ignore=kind)
        if kind == "college":
            return sorted({(p.college_key, p.college_label) for p in pool}, key=lambda o: o[1].casefold())
        if kind == "course":
            ids = {c for p in pool for c in p.courses}
            return sorted(((c, context.course_names.get(c, c)) for c in ids), key=lambda o: o[1].casefold())
        chosen = level.course or filter_course
        out = {(b[1], b[2]) for p in pool for b in p.batches if not chosen or b[0] == chosen}
        out = sorted(out, key=lambda o: o[1].casefold())
        if any(not [b for b in p.batches if not chosen or b[0] == chosen] for p in pool):
            out.append((hub.NO_BATCH, "No batch chosen"))
        return out

    level_for_row = hub.Level(level.college, level.course or filter_course or None, level.batch)
    response.headers["Cache-Control"] = "no-store"
    return {
        "lens": context.viewer.lens,
        "can": hub.capabilities(context.viewer),
        "level": level_out(context, level),
        "items": [hub.learner_row(context, p, level_for_row) for p in ordered[(page - 1) * limit : page * limit]],
        "total": len(ordered),
        "page": page,
        "limit": limit,
        "counts": {key: sum(1 for p in everyone if test(p)) for key, test in hub.status_filters(context).items()},
        "options": {
            "colleges": [{"value": k, "label": v} for k, v in options("college")],
            "courses": [{"value": k, "label": v} for k, v in options("course")],
            "batches": [{"value": k, "label": v} for k, v in options("batch")],
        },
    }


class Selection(BaseModel):
    """Either exact learners, or everyone matching a list, minus any unticked."""

    ids: list[int] = Field(default_factory=list, max_length=MAX_EXPORT)
    all_matching: bool = False
    exclude: list[int] = Field(default_factory=list, max_length=MAX_EXPORT)
    days: int = 7
    college: str = ""
    course: str = ""
    batch: str = ""
    filter_college: str = ""
    filter_course: str = ""
    filter_batch: str = ""
    status: str = "all"
    q: str = Field("", max_length=120)

    @model_validator(mode="after")
    def something(self):
        if not self.ids and not self.all_matching:
            raise ValueError("Choose at least one learner.")
        return self


async def resolve(user, session, selection: Selection):
    """Only learners this person may see can ever be selected, however the request is built.

    Resolved under the list the person was looking at, so "everyone matching" means exactly
    the people that list showed, whichever course an action then targets."""
    context = await hub.load_context(user, session, selection.days, selection.course or selection.filter_course or None)
    level = hub.effective_level(context, level_of(selection.college, selection.course, selection.batch))
    if selection.all_matching:
        skip = set(selection.exclude)
        people = [
            p
            for p in hub.match_learners(
                context, level, q=selection.q, status=selection.status, college=selection.filter_college,
                course=selection.filter_course, batch=selection.filter_batch,
            )
            if p.id not in skip
        ]
    else:
        wanted = set(selection.ids)
        people = [p for p in context.learners if p.id in wanted]
    return context, level, people


def require_action(can: bool, message: str):
    if not can:
        raise fastapi.HTTPException(403, message)


class Export(BaseModel):
    selection: Selection
    reason: str = Field(min_length=10, max_length=1000)

    _trim = field_validator("reason", mode="before")(classmethod(lambda c, v: v.strip() if isinstance(v, str) else v))


@router.post("/export")
async def export(
    body: Export,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    context, level, people = await resolve(user, session, body.selection)
    require_action(
        hub.capabilities(context.viewer)["export"],
        "An administrator must explicitly enable export for your account.",
    )
    if len(people) > MAX_EXPORT:
        raise fastapi.HTTPException(413, f"Export is limited to {MAX_EXPORT:,} learners. Narrow your filters.")
    out = io.StringIO()
    out.write("﻿")
    writer = csv.writer(out)
    writer.writerow(
        ["Learner ID", "Name", "College", "Courses", "Batch", "Last active", "Latest score",
         "Assessment average", "Score change", "Interviews", "Status"]
    )
    for person in hub.sort_learners(context, people, "urgent"):
        row = hub.learner_row(context, person, level)
        cells = [
            row["id"], row["name"], row["college"], "; ".join(c["name"] for c in row["courses"]),
            row["batch"] or "No batch chosen",
            row["last_active_at"].date().isoformat() if row["last_active_at"] else "Not yet",
            row["latest_score"] if row["latest_score"] is not None else "",
            row["assessment_average"] if row["assessment_average"] is not None else "",
            row["improvement"] if row["improvement"] is not None else "", row["interviews"], row["status_label"],
        ]
        writer.writerow([_safe_cell(c) for c in cells])
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action="learner_hub_exported", purpose="learner_reporting",
            target_type="learner_list", target_id="learner-hub", reason=body.reason,
            event_metadata={"rows": len(people), "status": body.selection.status, "all_matching": body.selection.all_matching},
        )
    )
    await session.commit()
    return fastapi.Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'attachment; filename="learners.csv"',
        },
    )


def _safe_cell(value):
    """A name starting with = or + must not run as a spreadsheet formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


class Nudge(BaseModel):
    selection: Selection
    message: str | None = Field(None, max_length=300)

    _trim = field_validator("message", mode="before")(classmethod(lambda c, v: v.strip() or None if isinstance(v, str) else v))


@router.post("/actions/nudge")
async def nudge(
    body: Nudge,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """A gentle in-app reminder. Nobody is reminded twice within a few days."""
    context, _, people = await resolve(user, session, body.selection)
    require_action(hub.capabilities(context.viewer)["coach"], "Reminders need your coaching responsibility.")
    if not people:
        raise fastapi.HTTPException(422, "No learners in your scope match this selection.")
    if len(people) > MAX_ACTION:
        raise fastapi.HTTPException(413, f"Remind up to {MAX_ACTION:,} learners at a time.")
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=NUDGE_GAP_DAYS)
    recent = set(
        (
            await session.execute(
                sa.select(Notification.recipient_id).where(
                    Notification.recipient_id.in_([p.id for p in people]),
                    Notification.kind == "learner_nudge",
                    Notification.created_at >= since,
                )
            )
        ).scalars()
    )
    sent, skipped = [], []
    for person in people:
        if person.id in recent:
            skipped.append({"id": person.id, "name": person.name, "reason": f"Already reminded in the last {NUDGE_GAP_DAYS} days."})
            continue
        notifications.notify(
            session, person.id, "learner_nudge", "Your next practice is waiting",
            body.message or "A short practice today keeps you on track.", "/",
        )
        session.add(
            NotificationDelivery(
                recipient_id=person.id, event="nudge", related_type="learner_nudge", related_id=person.id,
            )
        )
        sent.append({"id": person.id, "name": person.name})
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action="learner_hub_nudged", purpose="facilitator_coaching",
            target_type="learner_list", target_id="learner-hub",
            reason=body.message or "Reminder to practise.",
            event_metadata={"sent": [s["id"] for s in sent], "skipped": [s["id"] for s in skipped]},
        )
    )
    await session.commit()
    hub.forget(user.id)
    return {"done": sent, "skipped": skipped}


class Assign(BaseModel):
    selection: Selection
    course_id: str = Field(min_length=1, max_length=128)
    request_key: UUID
    kind: Literal["drill", "mock", "opening", "plan", "assessment"]
    module: Literal["warm_up", "think_fast", "pronunciation", "structure", "pace"] | None = None
    job_profile_id: int | None = Field(None, ge=1)
    opportunity_id: int | None = Field(None, ge=1)
    plan_id: int | None = Field(None, ge=1)
    reason: str = Field(min_length=10, max_length=1000)
    note: str | None = Field(None, max_length=2000)
    due_date: dt.date | None = None
    difficulty: Literal["easy", "medium", "hard", "expert"] = "medium"
    opens_at: dt.datetime | None = None
    closes_at: dt.datetime | None = None

    _trim = field_validator("reason", "note", mode="before")(classmethod(lambda c, v: v.strip() if isinstance(v, str) else v))


@router.post("/actions/assign")
async def assign(
    body: Assign,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Assign one thing to the selected learners in one course, each checked on their own."""
    context, _, people = await resolve(user, session, body.selection)
    if not people:
        raise fastapi.HTTPException(422, "No learners in your scope match this selection.")
    if len(people) > MAX_ACTION:
        raise fastapi.HTTPException(413, f"Assign to up to {MAX_ACTION:,} learners at a time.")
    inside = {p.id: p.name for p in people if body.course_id in p.courses}
    outside = [
        {"id": p.id, "name": p.name, "reason": "Not enrolled in this course."}
        for p in people
        if body.course_id not in p.courses
    ]
    if not inside:
        raise fastapi.HTTPException(422, "None of the selected learners are enrolled in this course.")
    if body.kind == "assessment":
        done, skipped = await _assign_assessment(session, user, body, inside)
    else:
        group = cohort_routes.Assign(
            request_key=body.request_key, kind=body.kind, module=body.module,
            job_profile_id=body.job_profile_id, opportunity_id=body.opportunity_id, plan_id=body.plan_id,
            reason=body.reason, note=body.note, due_date=body.due_date,
        )
        done, skipped = await cohort_routes.assign_group(
            session, user, body.course_id, inside, group, {"source": "learner_hub"}
        )
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action=f"learner_hub_assigned_{body.kind}", purpose="learner_hub",
            target_type="course", target_id=body.course_id, reason=body.reason,
            event_metadata={"done": [d["id"] for d in done], "skipped": [s["id"] for s in skipped + outside]},
        )
    )
    await session.commit()
    hub.forget(user.id)
    return {"done": done, "skipped": skipped + outside}


async def _assign_assessment(session, user, body: Assign, inside: dict[int, str]):
    from src.api.routes.opportunities import _assessment_template
    from src.services import assessment_assignments as assignments
    from src.services.interview_eligibility import unavailable_reason

    if body.job_profile_id is None or body.opens_at is None:
        raise fastapi.HTTPException(422, "Choose an assessment and when it opens.")
    if body.opens_at.tzinfo is None or (body.closes_at is not None and body.closes_at.tzinfo is None):
        raise fastapi.HTTPException(422, "Give times with a timezone.")
    if body.closes_at is not None and body.closes_at <= body.opens_at:
        raise fastapi.HTTPException(422, "The window must close after it opens.")
    predicate = await assignments.authorize(user, body.course_id, session, "create")
    template = await _assessment_template(session, body.job_profile_id)
    reason = unavailable_reason(template, body.difficulty)
    if reason:
        raise fastapi.HTTPException(422, reason)
    from src.services import template_scope

    template_scope.require_assignable(template, body.course_id)
    allowed = set(
        (await session.execute(sa.select(User.id).where(User.id.in_(inside), predicate))).scalars()
    )
    done, skipped = [], []
    from uuid import uuid5

    for sid, name in inside.items():
        if sid not in allowed:
            skipped.append({"id": sid, "name": name, "reason": "Outside your assessment responsibility."})
            continue
        await assignments.create_for(
            session, user, course_id=body.course_id, template=template, difficulty=body.difficulty,
            student_ids=[sid], opens_at=body.opens_at, closes_at=body.closes_at, instructions=body.note,
            request_key=uuid5(body.request_key, str(sid)),
        )
        done.append({"id": sid, "name": name})
    return done, skipped


class SetCollege(BaseModel):
    selection: Selection
    college: str = Field(min_length=2, max_length=256)

    _trim = field_validator("college", mode="before")(
        classmethod(lambda c, v: " ".join(v.split()) if isinstance(v, str) else v)
    )


@router.post("/actions/set-college")
async def set_college(
    body: SetCollege,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Record the college for learners who have none, or correct a wrong one."""
    context, _, people = await resolve(user, session, body.selection)
    require_action(hub.capabilities(context.viewer)["set_college"], "Only administrators and team leads can change a learner's college.")
    if not people:
        raise fastapi.HTTPException(422, "No learners in your scope match this selection.")
    if len(people) > MAX_ACTION:
        raise fastapi.HTTPException(413, f"Update up to {MAX_ACTION:,} learners at a time.")
    ids = [p.id for p in people]
    await session.execute(sa.update(User).where(User.id.in_(ids)).values(university=body.college))
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action="learner_hub_college_set", purpose="learner_records",
            target_type="learner_list", target_id="learner-hub", reason=f"Set college to {body.college}",
            event_metadata={"students": ids},
        )
    )
    await session.commit()
    hub._cache.clear()
    return {"done": [{"id": p.id, "name": p.name} for p in people], "skipped": []}


class ImportedCollege(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    college: str = Field(min_length=2, max_length=256)


class ImportColleges(BaseModel):
    rows: list[ImportedCollege] = Field(min_length=1, max_length=500)


@router.post("/import-colleges")
async def import_colleges(
    body: ImportColleges,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Keep the college from an import sheet for learners who have not joined yet."""
    viewer = await hub.viewer_for(user, session)
    require_action(hub.capabilities(viewer)["add_learners"], "Only administrators and team leads can import learners.")
    seen = 0
    for row in body.rows:
        email = row.email.strip().lower()
        college = " ".join(row.college.split())
        current = await session.get(LearnerImportCollege, email)
        if current:
            current.college, current.added_by = college, user.id
        else:
            session.add(LearnerImportCollege(email=email, college=college, added_by=user.id))
        seen += 1
    await session.commit()
    hub._cache.clear()
    return {"saved": seen}


def require_merge(context_viewer):
    require_action(hub.capabilities(context_viewer)["merge_colleges"], "Only administrators can merge college names.")


@router.get("/colleges/spellings")
async def spellings(
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    require_merge(await hub.viewer_for(user, session))
    return await hub.college_spellings(session)


class Merge(BaseModel):
    spellings: list[str] = Field(min_length=1, max_length=100)
    label: str = Field(min_length=2, max_length=256)

    _trim = field_validator("label", mode="before")(
        classmethod(lambda c, v: " ".join(v.split()) if isinstance(v, str) else v)
    )


@router.post("/colleges/merge")
async def merge(
    body: Merge,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Show these spellings as one college. The students' own records are not rewritten."""
    require_merge(await hub.viewer_for(user, session))
    canonical = hub.college_key(body.label)
    for raw in body.spellings:
        key = hub.college_key(raw)
        if key == hub.NOT_SET:
            raise fastapi.HTTPException(422, "A blank college cannot be merged. Use Set college instead.")
        row = await session.get(CollegeAlias, key)
        if row:
            row.canonical_key, row.canonical_label, row.created_by = canonical, body.label, user.id
        else:
            session.add(CollegeAlias(spelling_key=key, canonical_key=canonical, canonical_label=body.label, created_by=user.id))
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action="learner_hub_colleges_merged", purpose="learner_records",
            target_type="college", target_id=canonical[:64], reason=f"Merged into {body.label}",
            event_metadata={"spellings": body.spellings},
        )
    )
    await session.commit()
    hub._cache.clear()
    return {"ok": True}


@router.delete("/colleges/merge/{spelling}")
async def unmerge(
    spelling: str,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    require_merge(await hub.viewer_for(user, session))
    row = await session.get(CollegeAlias, hub.college_key(spelling))
    if row is None:
        raise fastapi.HTTPException(404, "This spelling is not merged.")
    await session.delete(row)
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action="learner_hub_colleges_unmerged", purpose="learner_records",
            target_type="college", target_id=spelling[:64], reason="Merge undone.", event_metadata={},
        )
    )
    await session.commit()
    hub._cache.clear()
    return {"ok": True}


@router.get("/learners/{student_id}")
async def learner_detail(
    student_id: int,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """The headline facts for one learner's page, under the same scope as every list."""
    context = await hub.load_context(user, session, 30)
    person = next((p for p in context.learners if p.id == student_id), None)
    if person is None:
        raise fastapi.HTTPException(404, "This learner is not in your scope.")
    counts = dict(
        (
            await session.execute(
                sa.select(Interview.interview_mode, sa.func.count(Interview.id))
                .where(Interview.user_id == student_id, Interview.status == "completed")
                .group_by(Interview.interview_mode)
            )
        ).all()
    )
    response.headers["Cache-Control"] = "no-store"
    return hub.learner_row(context, person, hub.Level()) | {
        "mock_interviews": int(counts.get("practice", 0)),
        "assessments": int(counts.get("assessment", 0)),
        "can": hub.capabilities(context.viewer),
    }
