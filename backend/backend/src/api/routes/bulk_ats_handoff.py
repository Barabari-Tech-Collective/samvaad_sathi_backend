"""Program passes screened people to Placement. Each step is checked, recorded and told to the other team."""

import datetime as dt
from collections import defaultdict

import fastapi
import pydantic
import sqlalchemy as sa

from src.api.dependencies.admin import (
    calculate_user_permissions,
    get_current_bulk_ats_user,
    get_user_active_memberships,
    is_admin_user,
    normalize_role,
)
from src.api.dependencies.session import get_async_session
from src.models.db.bulk_ats import BulkATSBatch, BulkATSCandidate, BulkATSHandoffMessage, BulkATSPitch
from src.models.db.notification import Notification
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.repository.crud.bulk_ats import BulkATSCRUDRepository
from src.services.ai_resume.bulk.deepseek_feedback import generate_company_pitch_summary
from src.services.email.bulk_ats_emails import build_company_pitch_email
from src.services.email.sender import send_email
from src.services.s3_service import generate_bulk_ats_resume_presigned_url

router = fastapi.APIRouter(prefix="/bulk-ats/handoff", tags=["bulk-ats-handoff"])
UTC = dt.timezone.utc
SCREENED = ("screened", "in_program_review", "approved_for_placement")
CARD = {"waiting": "shared_with_placement", "info": "needs_info", "accepted": "accepted_in_placement", "sent": "referred_to_hr", "declined": "rejected"}
LANDING = "/dashboard/samvaad/opportunities"
PROGRAM_ROLES = {"sr facilitator", "senior facilitator", "program lead", "program manager", "lead", "team lead", "creator"}
PLACEMENT_ROLES = {"placement lead", "placement_lead", "placement creator", "placement staff", "placement_staff", "placement coordinator", "placement_coordinator", "recruiter"}


class Who(pydantic.BaseModel):
    program: bool
    placement: bool


async def who_is(user, session) -> Who:
    if is_admin_user(user):
        return Who(program=True, placement=True)
    role = normalize_role(getattr(user, "_token_role", "") or getattr(user, "role", ""))
    program, placement = role in PROGRAM_ROLES, role in PLACEMENT_ROLES
    memberships = await get_user_active_memberships(user.id, session)
    if memberships:
        perms = calculate_user_permissions(user, memberships)
        program = program or bool(perms.get("can_review_program_handoff"))
        placement = placement or bool(perms.get("is_placement_team"))
    return Who(program=program, placement=placement)


async def require(user, session, team: str) -> Who:
    who = await who_is(user, session)
    if not getattr(who, team):
        raise fastapi.HTTPException(403, f"This needs {'Program' if team == 'program' else 'Placement'} team access.")
    return who


def href(card: str, batch_id: int | None) -> str:
    return f"{LANDING}?tab=shared&card={card}" + (f"&run={batch_id}" if batch_id else "")


def notes_to(session, ids, kind, title, body, card, batch_id):
    for rid in sorted(ids):
        session.add(Notification(recipient_id=rid, kind=kind, title=title[:160], body=body, href=href(card, batch_id), related_type="bulk_ats_batch", related_id=batch_id))


async def placement_people(session) -> set[int]:
    roles = list(PLACEMENT_ROLES)
    ids = set((await session.execute(sa.select(User.id).where(sa.func.lower(User.role).in_(roles)))).scalars())
    rows = (
        await session.execute(
            sa.select(TeamMember, Team).join(Team, TeamMember.team_id == Team.id).where(TeamMember.status == "active", Team.status == "active")
        )
    ).all()
    by_user = defaultdict(list)
    for member, team in rows:
        by_user[member.user_id].append((member, team))
    for uid, memberships in by_user.items():
        user = await session.get(User, uid)
        if user and calculate_user_permissions(user, memberships).get("is_placement_team"):
            ids.add(uid)
    ids |= set((await session.execute(sa.select(User.id).where(User.is_admin.is_(True)))).scalars())
    return ids


def log(session, candidate_id, user, kind, body):
    session.add(BulkATSHandoffMessage(candidate_id=candidate_id, author_id=user.id, author_name=user.name or user.email or "Staff", kind=kind, body=body))


async def prefills(session, batches) -> dict[int, dict]:
    """The company of each run's linked opening, and who was last contacted there."""
    from src.models.db.opportunity import Opportunity

    linked = {b.id: b.opportunity_id for b in batches if b.opportunity_id}
    if not linked:
        return {}
    orgs = dict((await session.execute(sa.select(Opportunity.id, Opportunity.organisation).where(Opportunity.id.in_(set(linked.values()))))).all())
    last: dict[str, tuple[str, str]] = {}
    rows = await session.execute(sa.select(BulkATSPitch.company_name, BulkATSPitch.hr_name, BulkATSPitch.hr_email).where(BulkATSPitch.company_name.in_(set(orgs.values()) or {""}), BulkATSPitch.delivered.is_(True)).order_by(BulkATSPitch.id))
    for company, hr, email in rows.all():
        last[company] = (hr, email)
    out = {}
    for batch_id, opp in linked.items():
        org = orgs.get(opp)
        if org:
            out[batch_id] = {"company": org, "hr_name": last.get(org, ("", ""))[0], "hr_email": last.get(org, ("", ""))[1]}
    return out


async def load(session, ids: list[int]) -> list[BulkATSCandidate]:
    rows = list(
        (
            await session.execute(
                sa.select(BulkATSCandidate).where(BulkATSCandidate.id.in_(ids), BulkATSCandidate.merged_into_id.is_(None)).with_for_update()
            )
        ).scalars()
    )
    if len(rows) != len(set(ids)):
        raise fastapi.HTTPException(404, "Some of these people are no longer available. Refresh and try again.")
    return rows


def only_from(rows, allowed, what):
    bad = [r.candidate_name or f"#{r.id}" for r in rows if r.handoff_status not in allowed]
    if bad:
        raise fastapi.HTTPException(409, f"{what} is not possible for {', '.join(bad[:3])}. Refresh to see where they are now.")


def single_run(rows):
    runs = {r.batch_id for r in rows}
    return rows[0].batch_id if len(runs) == 1 else None


def counted(n):
    return "1 person" if n == 1 else f"{n} people"


class Ids(pydantic.BaseModel):
    candidate_ids: list[int] = pydantic.Field(min_length=1, max_length=500)


class Share(Ids):
    readiness_score: int = pydantic.Field(ge=1, le=10)
    notes: str | None = pydantic.Field(default=None, max_length=5000)


class Ask(Ids):
    question: str = pydantic.Field(min_length=5, max_length=2000)


class Reply(pydantic.BaseModel):
    candidate_id: int
    reply: str = pydantic.Field(min_length=2, max_length=2000)


class Decline(Ids):
    reason: str = pydantic.Field(min_length=10, max_length=2000)


class Pitch(Ids):
    company_name: str = pydantic.Field(min_length=2, max_length=200)
    hr_name: str = pydantic.Field(min_length=2, max_length=200)
    hr_email: pydantic.EmailStr
    custom_notes: str | None = pydantic.Field(default=None, max_length=3000)
    include_resume_links: bool = True


def latest(messages, kind):
    return next((m for m in reversed(messages) if m.kind == kind), None)


def row_out(c, batch, messages, pitches, names, prefill=None):
    question, reply = latest(messages, "question"), latest(messages, "reply")
    open_reply = reply if reply and question and reply.created_at >= question.created_at and c.handoff_status == "shared_with_placement" else None
    last = pitches[-1] if pitches else None
    declined = latest(messages, "declined")
    return {
        "id": c.id, "batch_id": c.batch_id, "run": batch.name, "prefill": prefill or {}, "name": c.candidate_name or f"Candidate {c.id}",
        "email": c.email_or_candidate_id, "score": c.final_score, "band": c.score_band, "has_resume": bool(c.s3_key),
        "handoff_status": c.handoff_status, "decision": c.reviewer_decision, "readiness": c.program_readiness_score, "note": c.program_feedback_notes,
        "shared_by": names.get(c.program_reviewed_by), "shared_at": c.handoff_shared_at or c.program_reviewed_at,
        "question": question.body if question and c.handoff_status == "needs_info" else None, "reply": open_reply.body if open_reply else None,
        "accepted_by": names.get(c.placement_decided_by), "decline_reason": declined.body if c.handoff_status == "rejected" and declined else None,
        "placement_note": c.placement_notes,
        "pitch": {"id": last.id, "company": last.company_name, "hr_name": last.hr_name, "hr_email": last.hr_email, "delivered": last.delivered, "at": last.created_at} if last else None,
    }


@router.get("")
async def board(
    response: fastapi.Response,
    card: str = "waiting",
    run: int | None = None,
    q: str = fastapi.Query("", max_length=120),
    page: int = fastapi.Query(1, ge=1),
    limit: int = fastapi.Query(25, ge=1, le=100),
    user=fastapi.Depends(get_current_bulk_ats_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    who = await who_is(user, session)
    if not (who.program or who.placement):
        raise fastapi.HTTPException(403, "This needs Program or Placement team access.")
    c = BulkATSCandidate
    base = [c.merged_into_id.is_(None)]
    if run:
        base.append(c.batch_id == run)
    toshare = sa.and_(c.handoff_status.in_(SCREENED), c.reviewer_decision == "shortlist")
    conditions = {"toshare": toshare, **{k: c.handoff_status == v for k, v in CARD.items()}}
    counts = {}
    for key, condition in conditions.items():
        if key == "toshare" and not who.program:
            counts[key] = 0
            continue
        counts[key] = (await session.execute(sa.select(sa.func.count()).select_from(c).where(*base, condition))).scalar_one()
    needle = q.strip().lower()
    if needle:
        like = f"%{needle}%"
        visible = conditions["waiting"]
        for key, condition in conditions.items():
            if key != "waiting" and (key != "toshare" or who.program):
                visible = sa.or_(visible, condition)
        if who.program:
            visible = sa.or_(visible, sa.and_(c.handoff_status.in_(SCREENED), c.status.in_(["completed", "needs_review"])))
        where = [*base, visible, sa.or_(sa.func.lower(c.candidate_name).like(like), sa.func.lower(c.email_or_candidate_id).like(like))]
    else:
        if card not in conditions or (card == "toshare" and not who.program):
            card = "waiting"
        where = [*base, conditions[card]]
    total = (await session.execute(sa.select(sa.func.count()).select_from(c).where(*where))).scalar_one()
    rows = (
        await session.execute(
            sa.select(c, BulkATSBatch).join(BulkATSBatch, BulkATSBatch.id == c.batch_id).where(*where).order_by(
                c.final_score.desc().nulls_last() if card == "toshare" else c.handoff_shared_at.asc().nulls_last(), c.id
            ).offset((page - 1) * limit).limit(limit)
        )
    ).all()
    ids = [r[0].id for r in rows]
    messages, pitches = defaultdict(list), defaultdict(list)
    if ids:
        for m in (await session.execute(sa.select(BulkATSHandoffMessage).where(BulkATSHandoffMessage.candidate_id.in_(ids)).order_by(BulkATSHandoffMessage.id))).scalars():
            messages[m.candidate_id].append(m)
        for p in (await session.execute(sa.select(BulkATSPitch).where(BulkATSPitch.candidate_id.in_(ids)).order_by(BulkATSPitch.id))).scalars():
            pitches[p.candidate_id].append(p)
    user_ids = {r[0].program_reviewed_by for r in rows} | {r[0].placement_decided_by for r in rows}
    names = dict((await session.execute(sa.select(User.id, User.name).where(User.id.in_([u for u in user_ids if u] or [-1])))).all())
    fill = await prefills(session, [b for _, b in rows])
    items = [row_out(a, b, messages[a.id], pitches[a.id], names, fill.get(b.id)) for a, b in rows]
    if not needle and card == "waiting":
        items.sort(key=lambda x: x["reply"] is None)
    selectable = list((await session.execute(sa.select(c.id).where(*where).limit(500))).scalars()) if card in ("toshare", "waiting", "accepted") and not needle else []
    runs = (await session.execute(sa.select(BulkATSBatch.id, BulkATSBatch.name).order_by(BulkATSBatch.created_at.desc()).limit(200))).all()
    return {"items": items, "counts": counts, "total": total, "page": page, "limit": limit, "selectable": selectable, "can": who.model_dump(), "runs": [{"id": r.id, "name": r.name} for r in runs]}


@router.get("/{candidate_id}")
async def person(candidate_id: int, user=fastapi.Depends(get_current_bulk_ats_user), session=fastapi.Depends(get_async_session)):
    who = await who_is(user, session)
    if not (who.program or who.placement):
        raise fastapi.HTTPException(403, "This needs Program or Placement team access.")
    row = await session.get(BulkATSCandidate, candidate_id)
    if row is None or (not who.program and row.handoff_status in SCREENED):
        raise fastapi.HTTPException(404, "This person is not available.")
    batch = await session.get(BulkATSBatch, row.batch_id)
    messages = list((await session.execute(sa.select(BulkATSHandoffMessage).where(BulkATSHandoffMessage.candidate_id == candidate_id).order_by(BulkATSHandoffMessage.id.desc()))).scalars())
    pitches = list((await session.execute(sa.select(BulkATSPitch).where(BulkATSPitch.candidate_id == candidate_id).order_by(BulkATSPitch.id))).scalars())
    names = dict((await session.execute(sa.select(User.id, User.name).where(User.id.in_([u for u in (row.program_reviewed_by, row.placement_decided_by) if u] or [-1])))).all())
    return row_out(row, batch, list(reversed(messages)), pitches, names) | {
        "history": [{"kind": m.kind, "by": m.author_name, "body": m.body, "at": m.created_at} for m in messages],
        "can": who.model_dump(),
    }


class Note(pydantic.BaseModel):
    note: str = pydantic.Field(max_length=5000)


@router.post("/{candidate_id}/note")
async def save_note(candidate_id: int, body: Note, user=fastapi.Depends(get_current_bulk_ats_user), session=fastapi.Depends(get_async_session)):
    await require(user, session, "placement")
    rows = await load(session, [candidate_id])
    text = body.note.strip()
    rows[0].placement_notes = text or None
    log(session, candidate_id, user, "note", text or "Cleared the Placement note.")
    await session.commit()
    return {"saved": True}


@router.post("/share")
async def share(body: Share, user=fastapi.Depends(get_current_bulk_ats_user), session=fastapi.Depends(get_async_session)):
    await require(user, session, "program")
    rows = await load(session, body.candidate_ids)
    only_from(rows, SCREENED, "Sharing")
    now = dt.datetime.now(UTC)
    note = (body.notes or "").strip() or None
    for c in rows:
        c.handoff_status, c.program_readiness_score, c.program_feedback_notes = "shared_with_placement", body.readiness_score, note
        c.program_reviewed_by, c.program_reviewed_at, c.handoff_shared_at = user.id, now, now
        c.reviewer_decision = c.reviewer_decision or "shortlist"
        log(session, c.id, user, "shared", f"Rated {body.readiness_score} of 10. {note or ''}".strip())
    name = user.name or user.email or "Program"
    notes_to(session, await placement_people(session) - {user.id}, "placement_candidate_handoff", f"{name} shared {counted(len(rows))}", f"Rated {body.readiness_score} of 10.", "waiting", single_run(rows))
    await session.commit()
    return {"shared": len(rows)}


@router.post("/accept")
async def accept(body: Ids, user=fastapi.Depends(get_current_bulk_ats_user), session=fastapi.Depends(get_async_session)):
    await require(user, session, "placement")
    rows = await load(session, body.candidate_ids)
    only_from(rows, ("shared_with_placement", "needs_info"), "Accepting")
    now = dt.datetime.now(UTC)
    for c in rows:
        c.handoff_status, c.placement_decided_by, c.placement_decided_at = "accepted_in_placement", user.id, now
        log(session, c.id, user, "accepted", "")
    notes_to(session, {c.program_reviewed_by for c in rows if c.program_reviewed_by} - {user.id}, "placement_candidate_decision", f"{user.name or 'Placement'} accepted {counted(len(rows))}", None, "accepted", single_run(rows))
    await session.commit()
    return {"accepted": len(rows)}


@router.post("/ask")
async def ask(body: Ask, user=fastapi.Depends(get_current_bulk_ats_user), session=fastapi.Depends(get_async_session)):
    await require(user, session, "placement")
    rows = await load(session, body.candidate_ids)
    only_from(rows, ("shared_with_placement",), "Asking")
    text = body.question.strip()
    for c in rows:
        c.handoff_status = "needs_info"
        log(session, c.id, user, "question", text)
    notes_to(session, {c.program_reviewed_by for c in rows if c.program_reviewed_by} - {user.id}, "placement_candidate_question", f"{user.name or 'Placement'} asked about {counted(len(rows))}", text[:200], "info", single_run(rows))
    await session.commit()
    return {"asked": len(rows)}


@router.post("/reply")
async def reply(body: Reply, user=fastapi.Depends(get_current_bulk_ats_user), session=fastapi.Depends(get_async_session)):
    await require(user, session, "program")
    rows = await load(session, [body.candidate_id])
    only_from(rows, ("needs_info",), "Replying")
    c, text = rows[0], body.reply.strip()
    c.handoff_status = "shared_with_placement"
    log(session, c.id, user, "reply", text)
    asked = latest(list((await session.execute(sa.select(BulkATSHandoffMessage).where(BulkATSHandoffMessage.candidate_id == c.id, BulkATSHandoffMessage.kind == "question").order_by(BulkATSHandoffMessage.id))).scalars()), "question")
    notes_to(session, ({asked.author_id} if asked and asked.author_id else set()) - {user.id}, "placement_candidate_reply", f"{user.name or 'Program'} replied about {c.candidate_name or 'a candidate'}", text[:200], "waiting", c.batch_id)
    await session.commit()
    return {"replied": 1}


@router.post("/decline")
async def decline(body: Decline, user=fastapi.Depends(get_current_bulk_ats_user), session=fastapi.Depends(get_async_session)):
    await require(user, session, "placement")
    rows = await load(session, body.candidate_ids)
    only_from(rows, ("shared_with_placement", "needs_info", "accepted_in_placement"), "Declining")
    text, now = body.reason.strip(), dt.datetime.now(UTC)
    for c in rows:
        c.handoff_status, c.placement_decided_by, c.placement_decided_at = "rejected", user.id, now
        log(session, c.id, user, "declined", text)
    notes_to(session, {c.program_reviewed_by for c in rows if c.program_reviewed_by} - {user.id}, "placement_candidate_decision", f"{user.name or 'Placement'} declined {counted(len(rows))}", text[:200], "declined", single_run(rows))
    await session.commit()
    return {"declined": len(rows)}


@router.post("/move-back")
async def move_back(body: Ids, user=fastapi.Depends(get_current_bulk_ats_user), session=fastapi.Depends(get_async_session)):
    await require(user, session, "placement")
    rows = await load(session, body.candidate_ids)
    only_from(rows, ("rejected",), "Moving back")
    for c in rows:
        c.handoff_status = "shared_with_placement"
        log(session, c.id, user, "moved_back", "Moved back to review.")
    await session.commit()
    return {"moved": len(rows)}


async def _deliver(session, user, rows, batch, company, hr_name, hr_email, notes, links) -> bool:
    """One email per run. Every person gets a pitch record saying whether it really went."""
    summaries, skills = [], []
    for c in rows:
        metrics = (c.deterministic_report or {}).get("deterministicMetrics") or {}
        matched = (metrics.get("skillsModule") or {}).get("matchedSkills") or []
        skills.extend(s for s in matched if s and s not in skills)
        link = None
        if links and c.s3_key:
            link = generate_bulk_ats_resume_presigned_url(c.s3_key, expires_in=86400)
        elif links and c.resume_url:
            link = c.resume_url
        summaries.append({"name": c.candidate_name or f"Candidate {c.id}", "score": round(c.final_score, 1) if c.final_score is not None else None, "readiness_score": c.program_readiness_score, "matched_skills": matched[:6], "resume_url": link})
    try:
        summary = await generate_company_pitch_summary(company_name=company, role_name=batch.role_name, candidate_count=len(rows), top_skills=skills[:8])
        subject, html, text = build_company_pitch_email(company_name=company, hr_name=hr_name, role_name=batch.role_name, candidate_summaries=summaries, pitch_summary=summary, custom_notes=notes)
        sent = bool(send_email(to=hr_email, subject=subject, html=html, text=text))
    except Exception:
        sent = False
    now = dt.datetime.now(UTC)
    for c in rows:
        session.add(BulkATSPitch(candidate_id=c.id, company_name=company, hr_name=hr_name, hr_email=hr_email, custom_notes=notes, include_resume_links=links, delivered=sent, sent_by=user.id))
        c.handoff_status = "referred_to_hr"
        c.placement_decided_by, c.placement_decided_at = c.placement_decided_by or user.id, c.placement_decided_at or now
        c.placement_assigned_companies = sorted({*(c.placement_assigned_companies or []), company})
        log(session, c.id, user, "pitched" if sent else "pitch_failed", f"{hr_name}, {hr_email} at {company}")
    return sent


@router.post("/pitch")
async def pitch(body: Pitch, user=fastapi.Depends(get_current_bulk_ats_user), session=fastapi.Depends(get_async_session)):
    await require(user, session, "placement")
    rows = await load(session, body.candidate_ids)
    only_from(rows, ("shared_with_placement", "needs_info", "accepted_in_placement", "referred_to_hr", *SCREENED), "Pitching")
    runs = defaultdict(list)
    for c in rows:
        runs[c.batch_id].append(c)
    delivered, failed = 0, 0
    for batch_id, group in runs.items():
        ok = await _deliver(session, user, group, await session.get(BulkATSBatch, batch_id), body.company_name.strip(), body.hr_name.strip(), str(body.hr_email), (body.custom_notes or "").strip() or None, body.include_resume_links)
        delivered += len(group) if ok else 0
        failed += 0 if ok else len(group)
    notes_to(session, {c.program_reviewed_by for c in rows if c.program_reviewed_by} - {user.id}, "placement_candidate_decision", f"{user.name or 'Placement'} sent {counted(len(rows))} to {body.company_name.strip()}", None, "sent", single_run(rows))
    await session.commit()
    return {"delivered": delivered, "failed": failed}


@router.post("/pitches/{pitch_id}/resend")
async def resend(pitch_id: int, user=fastapi.Depends(get_current_bulk_ats_user), session=fastapi.Depends(get_async_session)):
    await require(user, session, "placement")
    old = await session.get(BulkATSPitch, pitch_id, with_for_update=True)
    if old is None:
        raise fastapi.HTTPException(404, "This email was not found.")
    if old.delivered:
        raise fastapi.HTTPException(409, "This email was already delivered.")
    rows = await load(session, [old.candidate_id])
    ok = await _deliver(session, user, rows, await session.get(BulkATSBatch, rows[0].batch_id), old.company_name, old.hr_name, old.hr_email, old.custom_notes, old.include_resume_links)
    await session.commit()
    return {"delivered": 1 if ok else 0, "failed": 0 if ok else 1}


