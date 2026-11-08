"""The staff Interviews area: one read model over AI, mock and human interviews.

Who may see a learner comes from the Learners hub, so a figure here always agrees with the same
figure there. Staff who only write questions get templates and never a learner. Staff who only
host interviews get the interviews they host. Writes stay in the routes that already own them;
this module adds the cross-course views those per-course routes cannot give.
"""

import base64
import binascii
import dataclasses
import datetime as dt
import json
from collections import defaultdict

import fastapi
import sqlalchemy as sa

from src.api.dependencies.admin import calculate_user_permissions, get_user_active_memberships, is_admin_user
from src.models.db.assessment_assignment import AssessmentAssignment
from src.models.db.cohort import Cohort
from src.models.db.feedback_issue import FeedbackIssue
from src.models.db.human_interview import HumanInterview
from src.models.db.human_scorecard import HumanScorecard
from src.models.db.integrity_case import IntegrityCase
from src.models.db.interview import Interview
from src.models.db.job_profile import JobProfile
from src.models.db.interview_question import InterviewQuestion
from src.models.db.job_profile_question import JobProfileQuestion
from src.models.db.notification import Notification, NotificationDelivery
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.question_attempt import QuestionAttempt
from src.models.db.student_placement import StudentPlacement
from src.models.db.user import User
from src.services import assessment_assignments as assignments
from src.services import human_interviews as human
from src.services import integrity_review as integrity
from src.services import learner_hub as lh
from src.services.analytics import AnalyticsService, _extract_overall_score
from src.services.feature_access import permits
from src.services.interview_question_plan import unique_questions
from src.services.people import student_only
from src.services.result_release import withheld

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BASE = "/dashboard/samvaad/interviews"
MAX_ROWS = 5000
PAGE_SIZES = (25, 50, 100)
OVERDUE_DAYS = 7
CLOSING_SOON_DAYS = 3
REMIND_GAP_HOURS = 24
OPEN_CASES = ("open", "appealed")


@dataclasses.dataclass(frozen=True)
class Viewer:
    user: User
    admin: bool
    people: lh.Viewer | None
    author: bool
    publish: bool
    human_read: bool
    human_create: bool
    human_review: bool
    assign: bool
    integrity: bool
    hosts: bool

    @property
    def author_only(self) -> bool:
        return self.people is None and self.author

    @property
    def host_only(self) -> bool:
        return self.people is None and not self.author

    def can(self) -> dict:
        return {
            "see_results": self.people is not None,
            "author": self.author,
            "publish": self.publish,
            "human": self.human_read or self.hosts,
            "human_create": self.human_create,
            "human_review": self.human_review,
            "assign": self.assign,
            "integrity": self.integrity,
            "operations": self.admin,
            "author_only": self.author_only,
            "host_only": self.host_only,
        }


async def _author(user: User, session) -> bool:
    if is_admin_user(user):
        return True
    memberships = await get_user_active_memberships(user.id, session)
    if not memberships:
        return False
    if isinstance(user.feature_permissions, dict):
        return permits(user.feature_permissions, "interview_authoring", "read")
    return bool(calculate_user_permissions(user, memberships).get("can_create_roles"))


def _may_publish(user: User) -> bool:
    return is_admin_user(user) or (
        isinstance(user.feature_permissions, dict)
        and permits(user.feature_permissions, "interview_authoring", "publish")
    )


async def viewer_for(user: User, session) -> Viewer:
    """Who is asking. Nobody is refused for lacking Learners if another part applies to them."""
    people = None
    try:
        people = await lh.viewer_for(user, session)
    except fastapi.HTTPException as refused:
        if refused.status_code != 403:
            raise
    author = await _author(user, session)
    hosts = bool(
        await session.scalar(sa.select(sa.exists().where(human.involved(user))))
    )
    if people is None and not author and not hosts:
        raise fastapi.HTTPException(403, "Interviews is not part of your role.")
    return Viewer(
        user=user,
        admin=is_admin_user(user),
        people=people,
        author=author,
        publish=_may_publish(user),
        human_read=human.permitted(user, "read"),
        human_create=human.permitted(user, "create"),
        human_review=human.permitted(user, "review"),
        assign=assignments.permitted(user, "create"),
        integrity=integrity.permitted(user, "read"),
        hosts=hosts,
    )


def _need_people(viewer: Viewer) -> lh.Viewer:
    if viewer.people is None:
        raise fastapi.HTTPException(403, "Interview results are not part of your role.")
    return viewer.people


def _need_author(viewer: Viewer) -> None:
    if not viewer.author:
        raise fastapi.HTTPException(403, "Writing interview questions is not part of your role.")


def _ist_start(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time.min, IST)


def _period_start(days: int) -> dt.datetime:
    today = dt.datetime.now(UTC).astimezone(IST).date()
    return _ist_start(today - dt.timedelta(days=days - 1))


def _days_since(at: dt.datetime | None, now: dt.datetime) -> int:
    return max(0, (now - at).days) if at else 0


def _parts(report_json) -> tuple[int | None, int | None]:
    """Knowledge and speech out of either report shape the app has stored."""
    if not isinstance(report_json, dict):
        return None, None
    summary = report_json.get("scoreSummary") or report_json.get("overallScoreSummary") or {}
    knowledge = (summary.get("knowledgeCompetence") or {}).get("percentage")
    speech = (summary.get("speechAndStructure") or summary.get("speechStructure") or {}).get("percentage")
    return knowledge, speech


async def _scores(session, ids: list[int]) -> dict[int, tuple[float, int | None, int | None]]:
    if not ids:
        return {}
    service = AnalyticsService(session)
    reports = await service._reports_by_interview(ids)
    summaries = await service._summary_reports_by_interview(ids)
    found = {}
    for iid in ids:
        value = _extract_overall_score(reports.get(iid), summaries.get(iid))
        if value is None:
            continue
        summary = summaries.get(iid)
        found[iid] = (value, *_parts(summary.report_json if summary else None))
    return found


# ---- Human interviews -------------------------------------------------------------------------


def stage_of(row: HumanInterview, card_status: str | None, now: dt.datetime) -> str:
    if row.status == "scheduled":
        return "attendance" if row.ends_at <= now else "upcoming"
    if row.status != "completed":
        return "closed"
    if row.result_released_at is not None:
        return "done"
    if card_status in (None, "draft", "returned"):
        return "scorecard"
    return "review" if card_status == "submitted" else "share"


def _human_scope(viewer: Viewer):
    if viewer.admin:
        return sa.true()
    mine = human.involved(viewer.user)
    if viewer.people is not None and viewer.human_read:
        return sa.or_(mine, lh._scope_clause(viewer.people))
    return mine


async def _human_rows(viewer: Viewer, session, *where):
    return (
        await session.execute(
            sa.select(HumanInterview, HumanScorecard.status, User.name, User.email, User.university)
            .join(User, User.id == HumanInterview.student_id)
            .outerjoin(HumanScorecard, HumanScorecard.appointment_id == HumanInterview.id)
            .where(student_only(), _human_scope(viewer), *where)
            .order_by(HumanInterview.starts_at.desc())
            .limit(MAX_ROWS)
        )
    ).all()


async def failed_invites(session, ids: list[int]) -> dict[int, int]:
    """Invitation emails that failed, newest wins, cleared by a later successful send."""
    if not ids:
        return {}
    rows = await session.execute(
        sa.select(
            NotificationDelivery.id, NotificationDelivery.related_id, NotificationDelivery.status
        )
        .where(
            NotificationDelivery.related_type == "human_interview",
            NotificationDelivery.related_id.in_(ids),
            NotificationDelivery.event.in_(("scheduled", "rescheduled")),
        )
        .order_by(NotificationDelivery.id)
    )
    latest = {}
    for delivery, related, status in rows:
        latest[related] = (delivery, status)
    return {k: d for k, (d, status) in latest.items() if status == "failed"}


def _host_name(row: HumanInterview, hosts: dict[int, str]) -> str:
    return row.interviewer_name or hosts.get(row.interviewer_id or row.host_id) or "Interviewer"


async def _host_names(session, rows) -> dict[int, str]:
    ids = {r[0].interviewer_id or r[0].host_id for r in rows}
    if not ids:
        return {}
    return dict((await session.execute(sa.select(User.id, User.name).where(User.id.in_(ids)))).all())


def _human_out(row, card, student, college, host, failed, can_host, now) -> dict:
    return {
        "id": row.id,
        "course_id": row.course_id,
        "student_id": row.student_id,
        "student": student,
        "college": college,
        "title": row.title,
        "role_name": row.role_name,
        "interviewer": host,
        "starts_at": row.starts_at,
        "ends_at": row.ends_at,
        "status": row.status,
        "stage": stage_of(row, card, now),
        "scorecard": card,
        "version": row.version,
        "can_join": human.joinable(row, now) and can_host,
        "invite_failed": row.id in failed,
        "invite_delivery_id": failed.get(row.id),
        "reschedule_count": row.reschedule_count,
    }


async def human_list(user, session, status, q, interviewer, page, size) -> dict:
    viewer = await viewer_for(user, session)
    if not (viewer.human_read or viewer.hosts):
        raise fastapi.HTTPException(403, "Human Interviews are not part of your role.")
    now = dt.datetime.now(UTC)
    rows = await _human_rows(viewer, session)
    hosts = await _host_names(session, rows)
    failed = await failed_invites(session, [r[0].id for r in rows if r[0].status == "scheduled"])
    term = (q or "").strip().casefold()
    picked = []
    for r in rows:
        host = _host_name(r[0], hosts)
        if term and term not in f"{r.name or ''} {r.email or ''} {r.university or ''}".casefold():
            continue
        if interviewer and host != interviewer:
            continue
        picked.append((r, host))
    counts: dict[str, int] = defaultdict(int)
    for r, _ in picked:
        counts[stage_of(r[0], r[1], now)] += 1
    counts["failed"] = sum(1 for r, _ in picked if r[0].id in failed)
    if status == "failed":
        shown = [(r, h) for r, h in picked if r[0].id in failed]
    elif status and status != "all":
        shown = [(r, h) for r, h in picked if stage_of(r[0], r[1], now) == status]
    else:
        shown = picked
    if status in ("upcoming", None, "all"):
        shown.sort(key=lambda x: (x[0][0].starts_at < now, abs((x[0][0].starts_at - now).total_seconds())))
    else:
        shown.sort(key=lambda x: x[0][0].starts_at)
    start = (page - 1) * size
    return {
        "total": len(shown),
        "page": page,
        "size": size,
        "counts": dict(counts) | {"all": len(picked)},
        "interviewers": sorted({h for _, h in picked}),
        "items": [
            _human_out(
                r[0], r[1], r.name or r.email, r.university or "College not set", h, failed,
                viewer.admin or r[0].host_id == user.id or r[0].interviewer_id == user.id, now,
            )
            for r, h in shown[start: start + size]
        ],
    }


# ---- Attempts ---------------------------------------------------------------------------------


async def _attempt_rows(context, session, of: str, days: int | None, *where):
    ids = [p.id for p in context.learners]
    if not ids:
        return []
    clauses = [
        Interview.user_id.in_(ids),
        Interview.interview_mode == of,
        Interview.status == "completed",
        *where,
    ]
    if days is not None:
        clauses.append(Interview.created_at >= _period_start(days))
    return (
        await session.execute(
            sa.select(Interview).where(*clauses).order_by(Interview.created_at.desc()).limit(MAX_ROWS)
        )
    ).scalars().all()


async def _names(session, ids) -> dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return dict((await session.execute(sa.select(JobProfile.id, JobProfile.job_name).where(JobProfile.id.in_(ids)))).all())


async def _open_cases(session, ids: list[int]) -> dict[int, IntegrityCase]:
    if not ids:
        return {}
    rows = await session.execute(
        sa.select(IntegrityCase).where(IntegrityCase.interview_id.in_(ids), IntegrityCase.state.in_(OPEN_CASES))
    )
    return {c.interview_id: c for c in rows.scalars()}


def _attempt_status(row: Interview, score) -> str:
    if score is None:
        return "delayed"
    return "held" if withheld(row) else "released"


def _batch_of(person, course_id):
    for course, key, name, _ in person.batches:
        if course == course_id:
            return key, name
    return None, None


async def attempts(
    user, session, of, days, q, job_profile_id, college, course, batch, difficulty, status, sort, page, size,
) -> dict:
    viewer = await viewer_for(user, session)
    _need_people(viewer)
    context = await lh.load_context(user, session, days or 30)
    people = {p.id: p for p in context.learners}
    rows = await _attempt_rows(context, session, of, days)
    scores = await _scores(session, [r.id for r in rows])
    jobs = await _names(session, [r.job_profile_id for r in rows])
    cases = await _open_cases(session, [r.id for r in rows]) if of == "assessment" and viewer.integrity else {}

    out = []
    for r in rows:
        person = people[r.user_id]
        batch_key, batch_name = _batch_of(person, r.course_id)
        got = scores.get(r.id)
        out.append(
            {
                "id": r.id,
                "student_id": r.user_id,
                "student": person.name,
                "college": person.college_label,
                "college_key": person.college_key,
                "course_id": r.course_id,
                "course": context.course_names.get(r.course_id, r.course_id) if r.course_id else None,
                "batch_key": batch_key,
                "batch": batch_name,
                "job_profile_id": r.job_profile_id,
                "job": jobs.get(r.job_profile_id) or r.track,
                "difficulty": r.difficulty,
                "score": got[0] if got else None,
                "knowledge": got[1] if got else None,
                "speech": got[2] if got else None,
                "duration_seconds": r.duration_seconds,
                "finished_at": r.completed_at or r.created_at,
                "status": _attempt_status(r, got) if of == "assessment" else ("released" if got else "delayed"),
                "integrity": r.id in cases,
            }
        )

    options = {
        "jobs": sorted({(x["job_profile_id"], x["job"]) for x in out if x["job_profile_id"]}, key=lambda j: j[1]),
        "colleges": sorted({(x["college_key"], x["college"]) for x in out}, key=lambda c: c[1]),
        "courses": sorted({(x["course_id"], x["course"]) for x in out if x["course_id"]}, key=lambda c: c[1]),
        "difficulties": sorted({x["difficulty"] for x in out if x["difficulty"]}),
    }
    term = (q or "").strip().casefold()
    def keep(x):
        return (
            (not term or term in f"{x['student']} {x['college']} {x['id']}".casefold())
            and (job_profile_id is None or x["job_profile_id"] == job_profile_id)
            and (not college or x["college_key"] == college)
            and (not course or x["course_id"] == course)
            and (not batch or x["batch_key"] == batch)
            and (not difficulty or x["difficulty"] == difficulty)
        )
    matched = [x for x in out if keep(x)]
    options["batches"] = sorted(
        {(x["batch_key"], x["batch"]) for x in out if x["batch_key"] and (not course or x["course_id"] == course)},
        key=lambda b: b[1] or "",
    )
    counts = {
        "all": len(matched),
        "held": sum(1 for x in matched if x["status"] == "held" and not x["integrity"]),
        "released": sum(1 for x in matched if x["status"] == "released" and not x["integrity"]),
        "delayed": sum(1 for x in matched if x["status"] == "delayed" and not x["integrity"]),
        "integrity": sum(1 for x in matched if x["integrity"]),
    }
    if status == "integrity":
        shown = [x for x in matched if x["integrity"]]
    elif status and status != "all":
        shown = [x for x in matched if x["status"] == status and not x["integrity"]]
    else:
        shown = matched
    if sort == "name":
        shown.sort(key=lambda x: x["student"].casefold())
    elif sort == "score":
        shown.sort(key=lambda x: (x["score"] is None, x["score"] or 0))
    else:
        shown.sort(key=lambda x: x["finished_at"], reverse=True)
    start = (page - 1) * size
    return {
        "of": of,
        "total": len(shown),
        "page": page,
        "size": size,
        "counts": counts,
        "options": {k: [{"value": a, "label": b} for a, b in v] if k != "difficulties" else v for k, v in options.items()},
        "can": viewer.can(),
        "items": shown[start: start + size],
    }


# ---- Templates and versions --------------------------------------------------------------------


@dataclasses.dataclass
class Family:
    root: int
    versions: list[JobProfile]  # published, oldest first
    draft: JobProfile | None

    @property
    def live(self) -> JobProfile | None:
        return self.versions[-1] if self.versions else None

    @property
    def head(self) -> JobProfile:
        return self.draft or self.live

    @property
    def ids(self) -> list[int]:
        return [p.id for p in self.versions] + ([self.draft.id] if self.draft else [])


def _allowed_courses(viewer: Viewer) -> set[str] | None:
    if viewer.people is None or viewer.people.admin or not viewer.people.grants:
        return None
    return {c for g in viewer.people.grants for c in g.course_ids}


def _visible(profile: JobProfile, allowed: set[str] | None) -> bool:
    return allowed is None or not profile.course_ids or bool(allowed & set(profile.course_ids))


async def families(viewer: Viewer, session, mode: str) -> list[Family]:
    rows = (
        await session.execute(sa.select(JobProfile).where(JobProfile.interview_mode == mode).order_by(JobProfile.id))
    ).scalars().all()
    parents = {p.id: p.parent_profile_id for p in rows}

    def root_of(pid):
        seen = set()
        while parents.get(pid) and parents[pid] in parents and pid not in seen:
            seen.add(pid)
            pid = parents[pid]
        return pid

    grouped: dict[int, list[JobProfile]] = defaultdict(list)
    for p in rows:
        grouped[root_of(p.id)].append(p)
    allowed = _allowed_courses(viewer)
    found = []
    for root, members in grouped.items():
        published = sorted(
            (p for p in members if p.status == "published"),
            key=lambda p: (p.published_at or p.created_at, p.id),
        )
        drafts = [p for p in members if p.status != "published"]
        family = Family(root, published, drafts[-1] if drafts else None)
        if _visible(family.head, allowed):
            found.append(family)
    return found


async def family_for(viewer: Viewer, session, root: int) -> Family:
    profile = await session.get(JobProfile, root)
    if profile is None:
        raise fastapi.HTTPException(404, "Interview not found.")
    for family in await families(viewer, session, profile.interview_mode):
        if root in family.ids or family.root == root:
            return family
    raise fastapi.HTTPException(404, "Interview not found.")


async def _course_names(session) -> dict[str, str]:
    rows = await session.execute(
        sa.select(StudentPlacement.course_id, StudentPlacement.course_name)
        .where(StudentPlacement.course_name.is_not(None))
        .distinct()
    )
    return dict(rows.all())


async def _readiness(session, profiles: list[JobProfile]) -> dict[int, dict]:
    """How many different questions each level has, and what is still short."""
    ids = [p.id for p in profiles]
    if not ids:
        return {}
    rows = (
        await session.execute(sa.select(JobProfileQuestion).where(JobProfileQuestion.job_profile_id.in_(ids)))
    ).scalars().all()
    by_profile: dict[int, list] = defaultdict(list)
    for q in rows:
        by_profile[q.job_profile_id].append(q)
    out = {}
    for p in profiles:
        need = p.questions_per_interview or 1
        counts = [len(unique_questions(q for q in by_profile[p.id] if q.level == level)) for level in (1, 2, 3, 4)]
        out[p.id] = {"counts": counts, "need": need, "short": [max(0, need - c) for c in counts]}
    return out


def _version_of(family: Family, profile: JobProfile) -> int:
    for number, p in enumerate(family.versions, 1):
        if p.id == profile.id:
            return number
    return len(family.versions) + 1


def _family_out(family: Family, names: dict[str, str], ready: dict[int, dict]) -> dict:
    live, draft = family.live, family.draft
    head = family.head
    return {
        "id": family.root,
        "head_id": head.id,
        "title": head.job_name,
        "kind": head.interview_mode,
        "courses": [{"id": c, "name": names.get(c, c)} for c in head.course_ids or []],
        "result_release": head.result_release,
        "live": {"id": live.id, "version": len(family.versions), "published_at": live.published_at} if live else None,
        "draft": {
            "id": draft.id,
            "status": draft.status,
            "version": len(family.versions) + 1,
            "note": draft.admin_comment,
            "revision": draft.content_revision,
            "generation_state": draft.generation_state,
            "readiness": ready.get(draft.id),
        }
        if draft
        else None,
    }


async def templates(user, session, mode, days, course) -> dict:
    viewer = await viewer_for(user, session)
    if viewer.host_only:
        raise fastapi.HTTPException(403, "Interview templates are not part of your role.")
    found = [f for f in await families(viewer, session, mode) if viewer.author or f.live]
    if course:
        found = [f for f in found if not f.head.course_ids or course in f.head.course_ids]
    names = await _course_names(session)
    ready = await _readiness(session, [f.draft for f in found if f.draft])
    stats: dict[int, dict] = {}
    if viewer.people is not None:
        context = await lh.load_context(user, session, days)
        ids = [p.id for p in context.learners]
        owner = {pid: f.root for f in found for pid in f.ids}
        rows = (
            await session.execute(
                sa.select(Interview).where(
                    Interview.user_id.in_(ids) if ids else sa.false(),
                    Interview.job_profile_id.in_(owner) if owner else sa.false(),
                    Interview.status == "completed",
                    Interview.interview_mode == mode,
                    *([Interview.course_id == course] if course else []),
                )
            )
        ).scalars().all()
        start = _period_start(days)
        recent = [r for r in rows if r.created_at >= start]
        scores = await _scores(session, [r.id for r in recent])
        for f in found:
            mine = [r for r in recent if owner[r.job_profile_id] == f.root]
            values = [scores[r.id][0] for r in mine if r.id in scores]
            stats[f.root] = {
                "finished": len(mine),
                "average": round(sum(values) / len(values), 1) if values else None,
                "held": sum(1 for r in rows if owner[r.job_profile_id] == f.root and withheld(r)),
            }
    items = [_family_out(f, names, ready) | {"stats": stats.get(f.root)} for f in found]
    items.sort(key=lambda x: (x["live"] is not None, x["title"].casefold()))
    return {"items": items, "can": viewer.can()}


# ---- Assignments, grouped the way staff made them ------------------------------------------------


def group_key(row: AssessmentAssignment) -> str:
    def at(value):
        return value.astimezone(UTC).isoformat() if value else ""

    raw = json.dumps([row.job_profile_id, row.cohort_id or 0, at(row.opens_at), at(row.closes_at), row.course_id])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def parse_key(key: str) -> tuple[int, int, dt.datetime, dt.datetime | None, str]:
    try:
        raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
        profile, cohort, opens, closes, course = json.loads(raw)
        return (
            int(profile), int(cohort), dt.datetime.fromisoformat(opens),
            dt.datetime.fromisoformat(closes) if closes else None, str(course),
        )
    except (binascii.Error, ValueError, TypeError) as bad:
        raise fastapi.HTTPException(404, "Assignment not found.") from bad


def _student_scope(viewer: Viewer):
    if viewer.admin:
        return sa.true()
    return lh._scope_clause(_need_people(viewer))


def _member_state(status: str) -> str:
    return {"started": "started", "completed": "completed", "cancelled": "cancelled"}.get(status, "not_started")


def _window_phase(rows, now) -> str:
    if all(r.status == "cancelled" for r in rows):
        return "cancelled"
    row = rows[0]
    if now < row.opens_at:
        return "upcoming"
    if row.closes_at is not None and now >= row.closes_at:
        return "closed"
    return "open"


async def _assignment_rows(viewer: Viewer, session, *where):
    rows = (
        await session.execute(
            sa.select(AssessmentAssignment, User.name, User.university)
            .join(User, User.id == AssessmentAssignment.student_id)
            .where(student_only(), _student_scope(viewer), *where)
            .order_by(AssessmentAssignment.opens_at.desc(), AssessmentAssignment.id)
            .limit(MAX_ROWS)
        )
    ).all()
    await assignments.settle(session, [r[0] for r in rows])
    return rows


def _group_rows(rows) -> dict[str, list]:
    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        groups[group_key(row[0])].append(row)
    return groups


async def _group_out(session, key, rows, now, jobs) -> dict:
    records = [r[0] for r in rows]
    first = records[0]
    cohort = await session.get(Cohort, first.cohort_id) if first.cohort_id else None
    states = [_member_state(r.status) for r in records if r.status != "cancelled"]
    label = cohort.name if cohort else (rows[0][1] if len(rows) == 1 else f"{len(rows)} learners")
    return {
        "key": key,
        "label": label,
        "job_profile_id": first.job_profile_id,
        "job": jobs.get(first.job_profile_id),
        "course_id": first.course_id,
        "opens_at": first.opens_at,
        "closes_at": first.closes_at,
        "phase": _window_phase(records, now),
        "total": len(states),
        "not_started": states.count("not_started"),
        "started": states.count("started"),
        "completed": states.count("completed"),
    }


async def template_detail(user, session, root) -> dict:
    viewer = await viewer_for(user, session)
    if viewer.host_only:
        raise fastapi.HTTPException(403, "Interview templates are not part of your role.")
    family = await family_for(viewer, session, root)
    if not viewer.author and family.live is None:
        raise fastapi.HTTPException(404, "Interview not found.")
    now = dt.datetime.now(UTC)
    names = await _course_names(session)
    ready = await _readiness(session, [family.head])
    out = _family_out(family, names, ready)
    head = family.head
    out["readiness"] = ready.get(head.id)
    out["details"] = {
        "job_name": head.job_name,
        "company_name": head.company_name,
        "category": head.category,
        "experience_level": head.experience_level,
        "employment_type": head.employment_type,
        "job_description": head.job_description,
        "skills": head.skills or [],
        "additional_context": head.additional_context,
        "questions_per_interview": head.questions_per_interview,
        "course_ids": head.course_ids or [],
    }
    counts = dict(
        (await session.execute(
            sa.select(Interview.job_profile_id, sa.func.count())
            .where(Interview.job_profile_id.in_(family.ids), Interview.status == "completed")
            .group_by(Interview.job_profile_id)
        )).all()
    ) if viewer.people is not None else {}
    out["versions"] = [
        {
            "version": n,
            "id": p.id,
            "published_at": p.published_at,
            "live": p.id == family.live.id,
            "attempts": counts.get(p.id) if viewer.people is not None else None,
        }
        for n, p in reversed(list(enumerate(family.versions, 1)))
    ]
    out["results"] = None
    if viewer.people is not None and family.live is not None:
        context = await lh.load_context(user, session, 30)
        out["results"] = await _results(viewer, session, context, family, now)
    out["can"] = viewer.can()
    return out


async def _results(viewer, session, context, family, now) -> dict:
    ids = [p.id for p in context.learners]
    people = {p.id: p for p in context.learners}
    rows = (
        await session.execute(
            sa.select(Interview)
            .where(
                Interview.user_id.in_(ids) if ids else sa.false(),
                Interview.job_profile_id.in_(family.ids),
                Interview.status == "completed",
            )
            .order_by(Interview.created_at.desc())
        )
    ).scalars().all()
    scores = await _scores(session, [r.id for r in rows])
    values = [scores[r.id][0] for r in rows if r.id in scores]
    groups: list[dict] = []
    if family.head.interview_mode == "assessment":
        assigned = await _assignment_rows(
            viewer, session, AssessmentAssignment.job_profile_id.in_(family.ids)
        )
        jobs = {family.root: family.head.job_name, **{p: family.head.job_name for p in family.ids}}
        grouped = _group_rows(assigned)
        groups = [await _group_out(session, k, v, now, jobs) for k, v in grouped.items()]
        groups.sort(key=lambda g: g["opens_at"], reverse=True)
    pending = sum(g["not_started"] for g in groups if g["phase"] != "cancelled")
    finished = len(rows)
    weak = await lh.weak_skills(session, [people[i] for i in {r.user_id for r in rows}])
    return {
        "assigned": finished + pending,
        "finished": finished,
        "shared": sum(1 for r in rows if r.id in scores and not withheld(r)),
        "held": sum(1 for r in rows if withheld(r)),
        "average": round(sum(values) / len(values), 1) if values else None,
        "recent": [
            {
                "id": r.id,
                "student": people[r.user_id].name,
                "college": people[r.user_id].college_label,
                "at": r.completed_at or r.created_at,
                "score": scores[r.id][0] if r.id in scores else None,
                "status": _attempt_status(r, scores.get(r.id)),
            }
            for r in rows[:5]
        ],
        "assignments": groups,
        "weak_skills": weak,
    }


async def assignment_group(user, session, key) -> dict:
    viewer = await viewer_for(user, session)
    profile, cohort, opens, closes, course = parse_key(key)
    rows = await _group_query(viewer, session, profile, cohort, opens, closes, course)
    now = dt.datetime.now(UTC)
    job = await session.get(JobProfile, profile)
    out = await _group_out(session, key, rows, now, {profile: job.job_name if job else "AI Interview"})
    ids = [r[0].student_id for r in rows]
    since = now - dt.timedelta(hours=REMIND_GAP_HOURS)
    reminded = set(
        (await session.execute(
            sa.select(Notification.recipient_id).where(
                Notification.recipient_id.in_(ids),
                Notification.kind == "learner_nudge",
                Notification.related_type == "assessment_assignment",
                Notification.created_at >= since,
            )
        )).scalars()
    )
    records = [r[0] for r in rows]
    order = {"not_started": 0, "started": 1, "completed": 2, "cancelled": 3}
    members = [
        {
            "id": r[0].id,
            "student_id": r[0].student_id,
            "student": r[1],
            "college": r[2] or "College not set",
            "state": _member_state(r[0].status),
            "reminded": r[0].student_id in reminded,
            "accommodations": r[0].accommodations or [],
            "interview_id": r[0].interview_id,
        }
        for r in rows
    ]
    members.sort(key=lambda m: (order[m["state"]], m["student"] or ""))
    return out | {
        "instructions": records[0].instructions,
        "difficulty": records[0].difficulty,
        "members": members,
        "accommodated": sum(1 for m in members if m["accommodations"]),
        "can": {
            "change": assignments.permitted(user, "update"),
            "remind": assignments.permitted(user, "create") or viewer.admin,
        },
    }


async def _group_query(viewer, session, profile, cohort, opens, closes, course):
    where = [
        AssessmentAssignment.job_profile_id == profile,
        AssessmentAssignment.course_id == course,
        AssessmentAssignment.opens_at == opens,
        AssessmentAssignment.cohort_id == cohort if cohort else AssessmentAssignment.cohort_id.is_(None),
        AssessmentAssignment.closes_at == closes
        if closes
        else AssessmentAssignment.closes_at.is_(None),
    ]
    rows = await _assignment_rows(viewer, session, *where)
    if not rows:
        raise fastapi.HTTPException(404, "Assignment not found.")
    return rows


# ---- The main page -----------------------------------------------------------------------------


def _item(id, title, detail, count, oldest, next_owner, href, icon, overdue=False) -> dict:
    return {
        "id": id, "title": title, "detail": detail, "count": count, "oldest_days": oldest,
        "overdue": overdue or oldest >= OVERDUE_DAYS, "next_owner": next_owner, "href": href, "icon": icon,
    }


async def _queue_author(viewer, session) -> tuple[list[dict], dict]:
    out: list[dict] = []
    tallies = {"drafts": 0, "review": 0, "live": 0, "issues": 0}
    if not viewer.author:
        return out, tallies
    found = []
    for mode in ("assessment", "practice"):
        found += await families(viewer, session, mode)
    ready = await _readiness(session, [f.draft for f in found if f.draft and f.draft.status == "draft"])
    now = dt.datetime.now(UTC)
    labels = ("Easy", "Medium", "Hard", "Expert")
    for f in found:
        tallies["live"] += 1 if f.live else 0
        if not f.draft:
            continue
        d = f.draft
        href = f"{BASE}/ai/{f.root}?tab=questions"
        if d.status in ("draft", "changes_requested"):
            tallies["drafts"] += 1
        if d.status == "under_review":
            tallies["review"] += 1
            if viewer.publish:
                out.append(_item(
                    f"review-{d.id}", f"Review questions: {d.job_name}", "Sent for review",
                    None, _days_since(d.submitted_at, now), "You", href, "check",
                ))
        elif d.status == "changes_requested":
            out.append(_item(
                f"changes-{d.id}", f"Changes asked: {d.job_name}", (d.admin_comment or "")[:80],
                None, _days_since(d.updated_at, now), "Writer", href, "message",
            ))
        elif viewer.author_only and not f.live:
            short = ready.get(d.id, {}).get("short", [])
            missing = [f"{n} more {labels[i]}" for i, n in enumerate(short) if n]
            out.append(_item(
                f"draft-{d.id}", f"Finish draft: {d.job_name}",
                f"Needs {', '.join(missing)}" if missing else "Ready to send for review",
                None, _days_since(d.updated_at, now), "Writer", href, "file",
            ))
    issues = await session.scalar(
        sa.select(sa.func.count()).select_from(FeedbackIssue).where(FeedbackIssue.status == "open")
    )
    tallies["issues"] = issues or 0
    if issues:
        oldest = await session.scalar(
            sa.select(sa.func.min(FeedbackIssue.created_at)).where(FeedbackIssue.status == "open")
        )
        out.append(_item(
            "issues", "Issues learners reported", "Transcript or feedback looks wrong",
            issues, _days_since(oldest, now), "You", f"{BASE}/issues", "alert",
        ))
    return out, tallies


async def _queue_human(viewer, session, now) -> tuple[list[dict], list[dict]]:
    if not (viewer.human_read or viewer.hosts):
        return [], []
    rows = await _human_rows(viewer, session, HumanInterview.status.in_(("scheduled", "completed")))
    hosts = await _host_names(session, rows)
    stages: dict[str, list] = defaultdict(list)
    for r in rows:
        stages[stage_of(r[0], r[1], now)].append(r)
    failed = await failed_invites(session, [r[0].id for r in rows if r[0].status == "scheduled"])

    def add(stage, title, detail, owner, icon, allowed=True):
        group = stages.get(stage) or []
        if not group or not allowed:
            return None
        oldest = max(_days_since(r[0].ends_at, now) for r in group)
        return _item(stage, title, detail, len(group), oldest, owner, f"{BASE}/human?status={stage}", icon)

    out = [
        x for x in (
            add("attendance", "Say what happened in a Human Interview", "The time has passed", "Interviewer", "clock"),
            add("scorecard", "Scorecards still to fill in", "After the interview", "Interviewer", "message"),
            add("review", "Scorecards to review", "Waiting for a second person", "You", "check", viewer.human_review),
            add("share", "Human results not shared", "Approved", "You", "send", viewer.human_review),
        ) if x
    ]
    if failed and viewer.human_read:
        out.append(_item(
            "invites", "Invitation emails that failed", "The interview is still booked",
            len(failed), 0, "You", f"{BASE}/human?status=failed", "alert",
        ))
    local = now.astimezone(IST).date()
    today = []
    for r in sorted(stages.get("upcoming") or [], key=lambda x: x[0].starts_at):
        row = r[0]
        if row.starts_at.astimezone(IST).date() != local:
            continue
        mine = viewer.admin or row.host_id == viewer.user.id or row.interviewer_id == viewer.user.id
        today.append(
            {
                "kind": "human",
                "id": row.id,
                "time": row.starts_at,
                "title": r.name or r.email,
                "detail": f"{row.title} · with {_host_name(row, hosts)}",
                "can_join": human.joinable(row, now) and mine,
                "href": f"/dashboard/samvaad/assessments/human/{row.id}",
                "join": f"/samvaad/v2/human-interviews/courses/{row.course_id}/{row.id}/join",
            }
        )
    for r in stages.get("upcoming") or []:
        row = r[0]
        mine = viewer.admin or row.host_id == viewer.user.id or row.interviewer_id == viewer.user.id
        if human.joinable(row, now) and mine:
            out.insert(0, _item(
                f"join-{row.id}", f"Join now: {r.name or r.email}", row.title, None, 0, "You",
                f"/dashboard/samvaad/assessments/human/{row.id}", "video",
            ))
    return out, today


async def _queue_results(viewer, session, context, course, now) -> tuple[list[dict], list[dict]]:
    out: list[dict] = []
    today: list[dict] = []
    ids = [p.id for p in context.learners]
    if not ids:
        return out, today
    where = [Interview.user_id.in_(ids), Interview.status == "completed", Interview.interview_mode == "assessment"]
    if course:
        where.append(Interview.course_id == course)
    if viewer.publish:
        rows = (
            await session.execute(
                sa.select(Interview.created_at)
                .where(*where, Interview.result_release == "review", Interview.result_released_at.is_(None))
            )
        ).scalars().all()
        if rows:
            out.append(_item(
                "held", "AI results held for review", "Learners cannot see these yet", len(rows),
                _days_since(min(rows), now), "You", f"{BASE}/attempts?of=assessment&status=held&all=1", "send",
            ))
    if viewer.integrity:
        cases = (
            await session.execute(
                sa.select(IntegrityCase.created_at)
                .join(Interview, Interview.id == IntegrityCase.interview_id)
                .where(*where, IntegrityCase.state.in_(OPEN_CASES))
            )
        ).scalars().all()
        if cases:
            out.append(_item(
                "integrity", "Integrity cases to decide", "Results stay held", len(cases),
                _days_since(min(cases), now), "You", f"{BASE}/integrity", "alert",
            ))
    if viewer.assign or viewer.admin:
        soon = now + dt.timedelta(days=CLOSING_SOON_DAYS)
        rows = await _assignment_rows(
            viewer, session,
            AssessmentAssignment.status == "assigned",
            *([AssessmentAssignment.course_id == course] if course else []),
            sa.or_(AssessmentAssignment.closes_at.is_(None), AssessmentAssignment.closes_at > now),
        )
        jobs = await _names(session, [r[0].job_profile_id for r in rows])
        local = now.astimezone(IST).date()
        for key, group in _group_rows(rows).items():
            first = group[0][0]
            info = await _group_out(session, key, group, now, jobs)
            closes = first.closes_at
            if closes and closes <= soon and now >= first.opens_at and info["not_started"]:
                left = (closes.astimezone(IST).date() - local).days
                when = "today" if left <= 0 else f"in {left} {'day' if left == 1 else 'days'}"
                out.append(_item(
                    f"closing-{key}", f"Closing {when}: {info['job'] or 'AI Interview'}", f"{info['label']} · not started",
                    info["not_started"], 0, "You", f"{BASE}/assignments/{key}", "clock",
                ))
            if first.opens_at.astimezone(IST).date() == local or (closes and closes.astimezone(IST).date() == local):
                closing = bool(closes and closes.astimezone(IST).date() == local)
                today.append(
                    {
                        "kind": "ai",
                        "id": key,
                        "time": closes if closing else first.opens_at,
                        "title": info["job"] or "AI Interview",
                        "detail": f"{'Closes' if closing else 'Opens'} · {info['label']} · {info['not_started']} not started",
                        "can_join": False,
                        "href": f"{BASE}/assignments/{key}",
                        "join": None,
                    }
                )
    if viewer.admin:
        ran = (
            await session.execute(sa.select(Interview.id, Interview.created_at).where(*where).order_by(Interview.created_at.desc()).limit(MAX_ROWS))
        ).all()
        scored = await _scores(session, [r.id for r in ran])
        late = [r.created_at for r in ran if r.id not in scored]
        if late:
            out.append(_item(
                "delayed", "AI reports delayed", "The answers are saved", len(late),
                _days_since(min(late), now), "Operations", f"{BASE}/attempts?of=assessment&status=delayed&all=1", "alert",
            ))
    return out, today


async def summary(user, session, days, college, course) -> dict:
    viewer = await viewer_for(user, session)
    now = dt.datetime.now(UTC)
    author_queue, tallies = await _queue_author(viewer, session)
    human_queue, today = await _queue_human(viewer, session, now)
    out = {
        "days": days,
        "can": viewer.can(),
        "author": tallies if viewer.author else None,
        "metrics": None,
        "filters": {"colleges": [], "courses": []},
        "queue": [],
        "today": today,
    }
    results_queue: list[dict] = []
    if viewer.people is not None:
        context = await lh.load_context(user, session, days, course)
        ids = {p.id for p in context.learners if not college or p.college_key == college}
        figures = lh.period_figures(context, ids)
        keep = [i for i in context.interviews if i.user_id in ids and i.status == "completed"]
        dates = [context.start + dt.timedelta(days=n) for n in range(days)]

        def in_range(row, lo, hi):
            return lo <= lh._ist_day(row.created_at) <= hi

        def counted(values_for):
            value = values_for(context.start, context.today)
            before = values_for(context.previous_start, context.start - dt.timedelta(days=1))
            return {
                "value": value, "previous": before, "change": value - before,
                "series": [values_for(d, d) for d in dates],
            }

        mock = counted(lambda lo, hi: sum(1 for i in keep if i.interview_mode == "practice" and in_range(i, lo, hi)))
        held = (
            await session.execute(
                sa.select(HumanInterview.starts_at)
                .join(User, User.id == HumanInterview.student_id)
                .where(
                    student_only(), _human_scope(viewer), HumanInterview.status == "completed",
                    HumanInterview.student_id.in_(ids) if ids else sa.false(),
                    *([HumanInterview.course_id == course] if course else []),
                    HumanInterview.starts_at >= _ist_start(context.previous_start),
                )
            )
        ).scalars().all()
        humans = counted(lambda lo, hi: sum(1 for at in held if lo <= lh._ist_day(at) <= hi))
        out["metrics"] = {
            "assessments": figures["metrics"]["assessments"],
            "average_score": figures["metrics"]["average_score"],
            "human": humans,
            "mock": mock,
        }
        full = await lh.load_context(user, session, days)
        out["filters"] = {
            "colleges": sorted(
                ({"value": k, "label": v} for k, v in {p.college_key: p.college_label for p in full.learners}.items()),
                key=lambda c: c["label"],
            ),
            "courses": sorted(
                ({"value": c, "label": full.course_names.get(c, c)} for c in {c for p in full.learners for c in p.courses}),
                key=lambda c: c["label"],
            ),
        }
        results_queue, ai_today = await _queue_results(viewer, session, context, course, now)
        out["today"] += ai_today
    queue = human_queue + results_queue + author_queue
    joins = [x for x in queue if x["id"].startswith("join-")]
    rest = sorted((x for x in queue if not x["id"].startswith("join-")), key=lambda x: -x["oldest_days"])
    out["queue"] = joins + rest
    return out


# ---- One attempt, and the integrity cases across courses -----------------------------------------


REPORT_RETRY = "report_retry_requested"
RETRY_QUIET_MINUTES = 15


def _answer_text(transcription) -> str | None:
    if isinstance(transcription, dict):
        text = transcription.get("text") or transcription.get("transcript")
        return text.strip() if isinstance(text, str) and text.strip() else None
    return None


async def _retry_requested_at(session, interview_id: int):
    since = dt.datetime.now(UTC) - dt.timedelta(minutes=RETRY_QUIET_MINUTES)
    return await session.scalar(
        sa.select(sa.func.max(PracticeDashboardAuditEvent.created_at)).where(
            PracticeDashboardAuditEvent.action == REPORT_RETRY,
            PracticeDashboardAuditEvent.target_id == str(interview_id),
            PracticeDashboardAuditEvent.created_at >= since,
        )
    )


async def attempt_detail(user, session, interview_id: int) -> dict:
    viewer = await viewer_for(user, session)
    _need_people(viewer)
    context = await lh.load_context(user, session, 30)
    people = {p.id: p for p in context.learners}
    row = await session.get(Interview, interview_id)
    if row is None or row.user_id not in people:
        raise fastapi.HTTPException(404, "Interview not found.")
    person = people[row.user_id]
    got = (await _scores(session, [row.id])).get(row.id)
    job = (await _names(session, [row.job_profile_id])).get(row.job_profile_id) or row.track
    questions = (
        await session.execute(
            sa.select(InterviewQuestion).where(InterviewQuestion.interview_id == row.id).order_by(InterviewQuestion.order)
        )
    ).scalars().all()
    attempts = (
        await session.execute(sa.select(QuestionAttempt).where(QuestionAttempt.interview_id == row.id))
    ).scalars().all()
    by_question = {a.question_id: a for a in attempts if a.question_id is not None}
    metrics = await AnalyticsService(session).get_interview_level_analytics(interview_id=row.id)
    scored = {q.get("question_id"): q for q in (metrics or {}).get("question_level", [])}
    items = []
    for number, q in enumerate(questions, 1):
        attempt, marks = by_question.get(q.id), scored.get(q.id, {})
        items.append(
            {
                "number": number,
                "question": q.text,
                "type": q.category,
                "is_follow_up": q.is_follow_up,
                "knowledge": marks.get("knowledge_score"),
                "speech": marks.get("speech_score"),
                "answer": _answer_text(attempt.transcription) if attempt else None,
                "feedback": attempt.feedback if attempt else None,
                "answered": attempt is not None and attempt.answer_saved_at is not None,
            }
        )
    capabilities = lh.capabilities(context.viewer)
    return {
        "id": row.id,
        "mode": row.interview_mode,
        "status": row.status,
        "student": {"id": person.id, "name": person.name, "college": person.college_label},
        "course_id": row.course_id,
        "course": context.course_names.get(row.course_id, row.course_id) if row.course_id else None,
        "job": job,
        "difficulty": row.difficulty,
        "finished_at": row.completed_at or row.created_at,
        "duration_seconds": row.duration_seconds,
        "score": got[0] if got else None,
        "knowledge": got[1] if got else None,
        "speech": got[2] if got else None,
        "delayed": row.status == "completed" and got is None,
        "retry_requested_at": await _retry_requested_at(session, row.id),
        "questions": items,
        "can": {"coach": capabilities["coach"], "retry_report": viewer.admin, "operations": viewer.admin},
        "coach": capabilities,
    }


async def integrity_cases(user, session, state: str, case_id: int | None, page: int, size: int) -> dict:
    viewer = await viewer_for(user, session)
    if not viewer.integrity:
        raise fastapi.HTTPException(403, "Integrity review is not part of your role.")
    allowed = _allowed_courses(viewer)
    where = [student_only(), _student_scope(viewer)]
    if allowed is not None:
        where.append(IntegrityCase.course_id.in_(allowed))
    rows = (
        await session.execute(
            sa.select(IntegrityCase, User.name, User.university, Interview.track, Interview.job_profile_id)
            .join(User, User.id == IntegrityCase.student_id)
            .join(Interview, Interview.id == IntegrityCase.interview_id)
            .where(*where)
            .order_by(IntegrityCase.created_at)
        )
    ).all()
    counts = {
        "open": sum(1 for r in rows if r[0].state in OPEN_CASES),
        "closed": sum(1 for r in rows if r[0].state not in OPEN_CASES),
    }
    if case_id is not None:
        shown = [r for r in rows if r[0].id == case_id]
    elif state == "closed":
        shown = [r for r in rows if r[0].state not in OPEN_CASES]
    else:
        shown = [r for r in rows if r[0].state in OPEN_CASES]
    jobs = await _names(session, [r.job_profile_id for r in shown])
    start, now = (page - 1) * size, dt.datetime.now(UTC)
    return {
        "total": len(shown),
        "page": page,
        "size": size,
        "counts": counts,
        "notice": integrity.NOTICE,
        "items": [
            integrity.serialize(r[0], user, now, staff=True)
            | {
                "student": r.name,
                "college": r.university or "College not set",
                "job": jobs.get(r.job_profile_id) or r.track,
                "opened_at": r[0].created_at,
            }
            for r in shown[start: start + size]
        ],
    }
