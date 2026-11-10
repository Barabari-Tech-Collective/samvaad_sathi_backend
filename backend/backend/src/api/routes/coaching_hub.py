"""The staff Coaching page: lists across courses. Assigning, plans and controls keep their own routes."""

import datetime as dt

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes import learning_plans as plan_routes
from src.models.db.coaching_assignment import CoachingAssignment
from src.models.db.cohort import Cohort, CohortMember
from src.models.db.learning_plan import LearningPlanAssignment
from src.models.db.mock_assignment import MockInterviewAssignment
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.user import User
from src.services import coaching_hub as service
from src.services import course_shortlists, learner_hub as hub, learning_plans as plans, notifications

router = fastapi.APIRouter(prefix="/v2/coaching-hub", tags=["coaching-hub"])
ORDER = {"low": 0, "idle": 1, "never": 2}
MODELS = {"c": (CoachingAssignment, "coaching_assignment"), "m": (MockInterviewAssignment, "mock_assignment"), "p": (LearningPlanAssignment, "learning_plan_assignment")}


async def scoped(user, session, college, course, batch):
    context = await hub.load_context(user, session, 30, course or None)
    level = hub.effective_level(context, hub.Level(college or None, course or None, batch or None))
    return context, hub.filter_learners(context, level)


def can_of(context, user) -> dict:
    caps = hub.capabilities(context.viewer)
    return {
        "coach": caps["coach"], "plans": plan_routes.can(user, "create"), "publish": plan_routes.can(user, "publish"),
        "shortlists": course_shortlists.permission(user, "read"), "is_admin": caps["is_admin"],
    }


def scope_options(context) -> list[dict]:
    colleges: dict[str, dict] = {}
    for person in context.learners:
        entry = colleges.setdefault(person.college_key, {"key": person.college_key, "label": person.college_label, "courses": {}})
        for course in person.courses:
            entry["courses"][course] = context.course_names.get(course, course)
    return [
        {"key": c["key"], "label": c["label"], "courses": [{"id": k, "name": v} for k, v in sorted(c["courses"].items(), key=lambda x: x[1])]}
        for c in sorted(colleges.values(), key=lambda x: x["label"])
    ]


@router.get("/learners")
async def learners(
    response: fastapi.Response,
    card: str = "need",
    sub: str = "all",
    q: str = fastapi.Query("", max_length=120),
    college: str = "",
    course: str = "",
    batch: str = "",
    page: int = fastapi.Query(1, ge=1),
    limit: int = fastapi.Query(25, ge=1, le=100),
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    context, people = await scoped(user, session, college, course, batch)
    by_id = {p.id: p for p in people}
    all_items = await service.load_items(session, context, set(by_id))
    if course:
        all_items = [i for i in all_items if i["course_id"] == course]
    busy = {i["student_id"] for i in all_items if i["status"] == "assigned"}
    needle = q.strip().lower()
    match = lambda p: not needle or needle in f"{p.name} {p.college_label}".lower()  # noqa: E731
    need = sorted(
        [p for p in people if p.status in ORDER and p.id not in busy],
        key=lambda p: (ORDER[p.status], p.name.lower()),
    )
    recent = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    def sets(pool):
        return {
            "open": [i for i in pool if i["status"] == "assigned" and (i["due_days"] is None or i["due_days"] >= 0)],
            "overdue": [i for i in pool if i["status"] == "assigned" and i["due_days"] is not None and i["due_days"] < 0],
            "done": [i for i in pool if i["status"] == "completed" and (i["closed_at"] or i["created_at"]) >= recent],
            "cancelled": [i for i in pool if i["status"] == "cancelled"],
        }
    top = sets(all_items)
    counts = {"need": len(need), "open": len(top["open"]), "overdue": len(top["overdue"]), "done": len(top["done"])}
    chips: list[dict] = []
    if card == "need":
        shown = [p for p in need if match(p)]
        everyone = sorted([p for p in people if match(p)], key=lambda p: p.name.lower())
        by = lambda k: [p for p in shown if p.status == k]  # noqa: E731
        chips = [
            {"key": "all", "label": "All", "count": len(shown)}, {"key": "low", "label": "Low score", "count": len(by("low"))},
            {"key": "idle", "label": "No activity", "count": len(by("idle"))}, {"key": "never", "label": "Never started", "count": len(by("never"))},
            {"key": "every", "label": "Everyone", "count": len(everyone)},
        ]
        rows = everyone if sub == "every" or needle else shown if sub == "all" else by(sub)
        total = len(rows)
        slice_ = rows[(page - 1) * limit: page * limit]
        ids = {p.id for p in slice_}
        drills, was = await service.drill_counts(session, ids), await service.nudged(session, ids)
        return {
            "kind": "need", "counts": counts, "chips": chips, "total": total, "page": page, "limit": limit,
            "rows": [service.person_out(context, p, p.id in busy, drills.get(p.id, 0), p.id in was) for p in slice_],
            "selectable": [p.id for p in rows][:2000],
            "selectable_people": [{"id": p.id, "name": p.name, "courses": [{"id": c, "name": context.course_names.get(c, c)} for c in p.courses]} for p in rows][:2000],
            "can": can_of(context, user), "scope": scope_options(context),
        }
    filtered = [i for i in all_items if not needle or needle in f"{by_id[i['student_id']].name} {by_id[i['student_id']].college_label}".lower()]
    groups = sets(filtered)
    if card == "done":
        chips = [{"key": "all", "label": "Finished", "count": len(groups["done"])}, {"key": "cancelled", "label": "Cancelled", "count": len(groups["cancelled"])}]
        rows = groups["cancelled"] if sub == "cancelled" else groups["done"]
    else:
        pool = groups.get(card, [])
        typed = lambda k: [i for i in pool if i["type"] == k]  # noqa: E731
        chips = [{"key": "all", "label": "All", "count": len(pool)}, {"key": "drill", "label": "Drills", "count": len(typed("drill"))},
                 {"key": "plan", "label": "Learning Plans", "count": len(typed("plan"))}, {"key": "mock", "label": "Mock Interviews", "count": len(typed("mock"))}]
        rows = pool if sub == "all" else typed(sub)
    rows = sorted(rows, key=lambda i: (i["due_days"] is None, i["due_days"] or 0))
    total = len(rows)
    slice_ = rows[(page - 1) * limit: page * limit]
    was = await service.reminded(session, slice_)
    out = []
    for item in slice_:
        p = by_id[item["student_id"]]
        out.append({k: v for k, v in item.items() if k != "steps"} | {"student": p.name, "college": p.college_label, "reminded": item["key"] in was})
    return {
        "kind": "item", "counts": counts, "chips": chips, "total": total, "page": page, "limit": limit, "rows": out,
        "selectable": [i["key"] for i in rows if i["status"] == "assigned"][:2000], "can": can_of(context, user), "scope": scope_options(context),
    }


class Remind(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=2000)


@router.post("/remind")
async def remind(body: Remind, user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session)):
    """A reminder for open practice. Nobody is reminded about the same item twice in a few days."""
    context = await hub.load_context(user, session, 30)
    if not hub.capabilities(context.viewer)["coach"]:
        raise fastapi.HTTPException(403, "Reminders need your coaching responsibility.")
    allowed = {p.id for p in context.learners}
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=service.REMIND_GAP_DAYS)
    sent, skipped = [], []
    for key in body.keys:
        spec = MODELS.get(key[:1])
        if spec is None or not key[1:].isdigit():
            raise fastapi.HTTPException(422, "Unknown item.")
        model, related = spec
        row = await session.get(model, int(key[1:]))
        if row is None or row.student_id not in allowed:
            skipped.append({"key": key, "reason": "Not in your scope."})
            continue
        if row.status not in ("assigned", "active"):
            skipped.append({"key": key, "reason": "No longer open."})
            continue
        existing = (
            await session.execute(
                sa.select(service.Notification.id).where(
                    service.Notification.kind == "coaching_reminder", service.Notification.related_type == related,
                    service.Notification.related_id == row.id, service.Notification.created_at >= since,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if existing:
            skipped.append({"key": key, "reason": "Already reminded in the last few days."})
            continue
        notifications.notify(session, row.student_id, "coaching_reminder", "A reminder about your practice", "Your teacher is waiting for you to finish this practice.", "/coaching", (related, row.id))
        sent.append(key)
    session.add(PracticeDashboardAuditEvent(actor_id=user.id, action="coaching_reminded", purpose="facilitator_coaching", target_type="coaching_list", target_id="coaching-hub", reason="Reminder about open practice.", event_metadata={"sent": sent}))
    await session.commit()
    return {"sent": sent, "skipped": skipped}


@router.get("/groups")
async def groups(user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session)):
    """Batches and cohorts this person can assign to at once, with their members."""
    context = await hub.load_context(user, session, 30)
    pick = lambda p: {"id": p.id, "name": p.name, "courses": [{"id": c, "name": context.course_names.get(c, c)} for c in p.courses]}  # noqa: E731
    by_id = {p.id: p for p in context.learners}
    batches: dict[tuple[str, str], dict] = {}
    for person in context.learners:
        for course, key, name, *_ in person.batches:
            entry = batches.setdefault((course, key), {"key": f"b:{course}:{key}", "label": f"Batch: {name} ({context.course_names.get(course, course)})", "people": []})
            entry["people"].append(pick(person))
    out = sorted(batches.values(), key=lambda x: x["label"])
    courses = {c for p in context.learners for c in p.courses}
    rows = (
        await session.execute(
            sa.select(Cohort.id, Cohort.name, Cohort.course_id, CohortMember.student_id)
            .join(CohortMember, CohortMember.cohort_id == Cohort.id)
            .where(Cohort.archived_at.is_(None), Cohort.course_id.in_(courses or {""}))
        )
    ).all()
    cohorts: dict[int, dict] = {}
    for cid, name, course, student in rows:
        if student in by_id:
            cohorts.setdefault(cid, {"key": f"c:{cid}", "label": f"Cohort: {name} ({context.course_names.get(course, course)})", "people": []})["people"].append(pick(by_id[student]))
    return {"items": out + sorted(cohorts.values(), key=lambda x: x["label"])}


@router.get("/drills")
async def drills(college: str = "", course: str = "", batch: str = "", user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session)):
    _, people = await scoped(user, session, college, course, batch)
    ids = [p.id for p in people] or [-1]
    counts: dict[tuple[str, str], int] = {
        (m, s): n
        for m, s, n in (
            await session.execute(
                sa.select(CoachingAssignment.module, CoachingAssignment.status, sa.func.count())
                .where(CoachingAssignment.student_id.in_(ids))
                .group_by(CoachingAssignment.module, CoachingAssignment.status)
            )
        ).all()
    }
    return {"items": [{"module": k, "name": v[0], "what": v[1], "why": v[2], "open": counts.get((k, "assigned"), 0), "finished": counts.get((k, "completed"), 0)} for k, v in service.DRILLS.items()]}


@router.get("/plans")
async def plans_list(status: str | None = None, user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session)):
    plan_routes.require(user, "read")
    context = await hub.load_context(user, session, 30)
    return {"items": await service.plan_rows(session, {p.id for p in context.learners}, status), "can": can_of(context, user)}


@router.get("/plans/{plan_id}")
async def plan_detail(plan_id: int, user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session)):
    plan_routes.require(user, "read")
    context = await hub.load_context(user, session, 30)
    by_id = {p.id: p for p in context.learners}
    plan = await plan_routes.load_plan(session, plan_id)
    detail = await plan_routes.plan_detail(session, plan)
    rows = list(
        (
            await session.execute(
                sa.select(LearningPlanAssignment).where(
                    LearningPlanAssignment.plan_id == plan_id, LearningPlanAssignment.student_id.in_(list(by_id) or [-1])
                )
            )
        ).scalars()
    )
    views = await plans.assignment_views(session, rows)
    finished: dict[int, int] = {}
    who = []
    for row, view in zip(rows, views):
        done = 0
        for step in view["steps"]:
            if step["status"] == "done":
                done += 1
                finished[step["id"]] = finished.get(step["id"], 0) + 1
        who.append({"assignment_id": row.id, "student_id": row.student_id, "course_id": row.course_id, "name": by_id[row.student_id].name, "college": by_id[row.student_id].college_label, "status": row.status, "version": row.version, "done": done, "total": len(view["steps"]), "self_started": row.source != "staff", "due_date": row.due_date})
    return detail | {"finished_by_step": finished, "who": who, "can": can_of(context, user)}


@router.get("/practice")
async def practice(signal: str = "all", college: str = "", course: str = "", batch: str = "", page: int = fastapi.Query(1, ge=1), limit: int = fastapi.Query(25, ge=1, le=100), user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session)):
    context, people = await scoped(user, session, college, course, batch)
    return await service.practice_board(session, {p.id for p in people}, signal, page, limit) | {"can": can_of(context, user)}


@router.get("/learners/{student_id}/coaching")
async def learner_coaching(student_id: int, user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session)):
    """Everything set for one learner, with step-by-step plan progress."""
    context = await hub.load_context(user, session, 30)
    person = next((p for p in context.learners if p.id == student_id), None)
    if person is None:
        raise fastapi.HTTPException(404, "This learner is not in your scope.")
    items = await service.load_items(session, context, {student_id})
    items.sort(key=lambda i: i["created_at"], reverse=True)
    was = await service.reminded(session, items)
    for item in items:
        item["reminded"] = item["key"] in was
    drills = await service.drill_counts(session, {student_id})
    return {
        "items": items, "learner": service.person_out(context, person, any(i["status"] == "assigned" for i in items), drills.get(student_id, 0), False),
        "can": can_of(context, user),
    }
