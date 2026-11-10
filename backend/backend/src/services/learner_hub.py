"""The Learners hub: one server-scoped view of colleges, courses, batches and learners.

Every figure, list, export and bulk action starts from `load_context`, which only ever holds the
students this staff member may see. The menu is never the boundary. A course is not a child of
a college in the data: a college's courses are the courses its learners are enrolled in.

Colleges are free text typed by students, so they are grouped case and space insensitively and
shown under the most common spelling. Students with no college or no batch are reported as such.
"""

import datetime as dt
import re
import time
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import fastapi
import sqlalchemy as sa

from src.api.dependencies.admin import (
    calculate_user_permissions,
    get_user_active_memberships,
    get_user_allowed_colleges,
    is_admin_user,
)
from src.models.db.assessment_assignment import AssessmentAssignment
from src.models.db.coaching_assignment import CoachingAssignment
from src.models.db.college_alias import CollegeAlias, LearnerImportCollege
from src.models.db.interview import Interview
from src.models.db.job_profile import JobProfile
from src.models.db.opportunity import OpportunityCandidate
from src.models.db.practice_drill_completion import PracticeDrillCompletion
from src.models.db.question_attempt import QuestionAttempt
from src.models.db.report import Report
from src.models.db.student_placement import StudentPlacement
from src.models.db.summary_report import SummaryReport
from src.models.db.user import User
from src.services import readiness
from src.services.assessment_assignments import settle as settle_assignments
from src.services.analytics import AnalyticsService, _extract_overall_score
from src.services.course_access import CourseGrant, membership_grants, student_predicate
from src.services.feature_access import permits
from src.services.people import student_only
from src.services.sampark_enrollment_sync import freshness_predicate

UTC = dt.timezone.utc
IST = ZoneInfo("Asia/Kolkata")
PERIODS = (1, 3, 7, 15, 30)
NOT_SET = "not_set"
NO_BATCH = "none"
NO_COURSE = "none"
IDLE_DAYS = 7
LOW_SCORE = 60.0
HIGH_SCORE = 75.0
# A figure from fewer people than this says more about them than about the group.
MIN_EVIDENCE = 5
COMPARE_DAYS = 60
IMPROVED_POINTS = 5
DEFAULT_DUE_DAYS = 7
STALE_DAYS = 45
OVERDUE_DAYS = 14
CACHE_SECONDS = 20
CACHE_ENTRIES = 64
STATUS_LABELS = {
    "never": "Never started",
    "idle": "No activity",
    "low": "Low score twice",
    "ready": "Ready",
    "ok": "On track",
}
NEEDS_ACTION = ("never", "idle", "low")
URGENCY = {"idle": 0, "never": 1, "low": 2, "ready": 3, "ok": 4}


@dataclass(frozen=True)
class Viewer:
    user: User
    admin: bool
    lens: str  # admin | program | placement
    lead: bool
    grants: tuple[CourseGrant, ...]
    colleges: frozenset[str] | None  # None means every college
    policy: dict | None


@dataclass
class Learner:
    id: int
    name: str
    email: str | None
    college_key: str
    college_label: str
    courses: tuple[str, ...]
    batches: list[tuple[str, str, str, str | None]] = field(default_factory=list)
    created_at: dt.datetime | None = None
    last_at: dt.datetime | None = None
    last_score: float | None = None
    previous_score: float | None = None
    last_scored_at: dt.datetime | None = None
    ready: bool = False
    put_forward: bool = False
    interviews: int = 0
    assessment_average: float | None = None
    # Latest minus first score, same job role and difficulty, inside COMPARE_DAYS.
    improvement: float | None = None
    knowledge: float | None = None
    speech: float | None = None
    status: str = "ok"
    since: dt.datetime | None = None

    @property
    def stale(self) -> bool:
        return (
            self.status == "ready"
            and self.last_scored_at is not None
            and self.last_scored_at < dt.datetime.now(UTC) - dt.timedelta(days=STALE_DAYS)
        )


@dataclass
class Context:
    viewer: Viewer
    days: int
    learners: list[Learner]
    interviews: list  # (id, user_id, status, mode, created_at)
    scores: dict[int, float]
    today: dt.date
    start: dt.date
    previous_start: dt.date
    course_names: dict[str, str]
    candidates_at: dict[int, dt.datetime]
    drills: list  # (user_id, completed_at)
    assignments: list[tuple[int, bool]]  # (student_id, finished) for windows closed in the period
    coaching: list[tuple[int, bool]]  # (student_id, finished) for drills due in the period
    active_ids: set[int]
    interviewed_ids: set[int]
    assessed_ids: set[int]


_cache: "OrderedDict[tuple, tuple[float, Context]]" = OrderedDict()


def forget(user_id: int) -> None:
    """Drop this person's cached view after they change something."""
    for key in [k for k in _cache if k[0] == user_id]:
        _cache.pop(key, None)


def college_key(value: str | None) -> str:
    return " ".join((value or "").split()).casefold() or NOT_SET


async def _aliases(session) -> dict[str, tuple[str, str]]:
    rows = await session.execute(
        sa.select(CollegeAlias.spelling_key, CollegeAlias.canonical_key, CollegeAlias.canonical_label)
    )
    return {spelling: (key, label) for spelling, key, label in rows}


_ABBREVIATIONS = {
    "clg": "college", "coll": "college", "col": "college", "univ": "university", "uni": "university",
    "engg": "engineering", "engr": "engineering", "inst": "institute", "tech": "technology", "govt": "government",
}


def simplified(value: str) -> str:
    """A rough form for spotting likely duplicates. Never used to merge on its own."""
    words = re.sub(r"[^a-z0-9 ]", " ", value.casefold()).split()
    return " ".join(_ABBREVIATIONS.get(w, w) for w in words if w not in ("of", "the", "and"))


async def college_spellings(session) -> dict:
    """Every spelling in use, what it is merged into, and groups that look like one college."""
    aliases = await _aliases(session)
    rows = await session.execute(
        sa.select(User.university, sa.func.count(User.id))
        .where(student_only(), User.university.is_not(None))
        .group_by(User.university)
    )
    counted: dict[str, Counter] = defaultdict(Counter)
    for raw, n in rows:
        key = college_key(raw)
        if key != NOT_SET:
            counted[key][" ".join(raw.split())] += n
    spellings = [
        {
            "key": key,
            "label": names.most_common(1)[0][0],
            "learners": sum(names.values()),
            "merged_into": aliases[key][1] if key in aliases else None,
        }
        for key, names in counted.items()
    ]
    spellings.sort(key=lambda s: (-s["learners"], s["label"].casefold()))
    free = [s for s in spellings if s["merged_into"] is None]
    groups: list[list[dict]] = []
    used: set[str] = set()
    for one in free:
        if one["key"] in used:
            continue
        base = simplified(one["label"])
        group = [one]
        for other in free:
            if other["key"] == one["key"] or other["key"] in used:
                continue
            target = simplified(other["label"])
            if base == target or SequenceMatcher(None, base, target).ratio() >= 0.88:
                group.append(other)
        if len(group) > 1:
            used.update(s["key"] for s in group)
            groups.append(group)
    return {
        "spellings": spellings,
        "suggestions": [
            {"label": g[0]["label"], "spellings": [{k: s[k] for k in ("key", "label", "learners")} for s in g]}
            for g in groups
        ],
    }


async def viewer_for(user: User, session) -> Viewer:
    """Who is asking and what they may see. Refuses anyone the Learners area is not for."""
    if is_admin_user(user):
        return Viewer(user, True, "admin", True, (), None, None)
    memberships = await get_user_active_memberships(user.id, session)
    if not memberships:
        raise fastapi.HTTPException(403, "An active staff membership is required.")
    perms = calculate_user_permissions(user, memberships)
    # The same rule as the staff menu, so nobody is shown a page that then refuses them.
    if not (perms.get("can_access_students") or perms.get("is_team_lead")):
        raise fastapi.HTTPException(403, "Learners is not part of your role.")
    policy = user.feature_permissions
    if isinstance(policy, dict) and not (
        permits(policy, "analytics", "read")
        or permits(policy, "course_workspace", "read")
        or permits(policy, "placement_shortlists", "read")
    ):
        raise fastapi.HTTPException(403, "Learners has been turned off for your account.")
    placement = program = False
    for item in memberships:
        team = item[1] if isinstance(item, (tuple, list)) and len(item) > 1 else getattr(item, "team", None)
        name = (getattr(team, "name", "") or "").casefold()
        if any(word in name for word in ("placement", "partnership")):
            placement = True
        else:
            program = True
    grants = membership_grants(memberships)
    colleges = None
    aliases = await _aliases(session)
    def with_spellings(names):
        """Staff assigned to a college also cover the spellings merged into it."""
        wanted = {aliases[k][0] if k in aliases else k for k in names}
        return frozenset(wanted | {k for k, (canon, _) in aliases.items() if canon in wanted})
    if grants:
        grants = tuple(
            CourseGrant(g.course_ids, None if g.colleges is None else with_spellings(g.colleges)) for g in grants
        )
    else:
        allowed = await get_user_allowed_colleges(user, session)
        colleges = None if allowed is None else with_spellings(college_key(c) for c in allowed)
    return Viewer(
        user,
        False,
        "placement" if placement and not program else "program",
        bool(perms.get("is_team_lead")) or bool(perms.get("can_edit")),
        grants,
        colleges,
        policy if isinstance(policy, dict) else None,
    )


def _scope_clause(viewer: Viewer):
    if viewer.admin:
        return sa.true()
    if viewer.grants:
        clauses = []
        for course_id in {c for g in viewer.grants for c in g.course_ids}:
            clauses.append(student_predicate(course_id, viewer.grants, global_access=False))
        return sa.or_(*clauses) if clauses else sa.false()
    if viewer.colleges is None:
        return sa.true()
    if not viewer.colleges:
        return sa.false()
    spelled = sa.func.regexp_replace(sa.func.btrim(User.university), r"\s+", " ", "g")
    return sa.func.lower(spelled).in_(list(viewer.colleges))


def _visible_courses(viewer: Viewer, college: str | None, enrolled) -> tuple[str, ...]:
    courses = sorted({c for c in (enrolled or []) if isinstance(c, str) and c.strip()})
    if viewer.admin or not viewer.grants:
        return tuple(courses)
    return tuple(c for c in courses if any(g.allows(c, college) for g in viewer.grants))


def _interview_course(course_id: str | None):
    """Inside a course: its interviews, plus older ones never tagged with any course."""
    if not course_id or course_id == NO_COURSE:
        return sa.true()
    return sa.or_(Interview.course_id == course_id, Interview.course_id.is_(None))


def _classify(person: Learner, now: dt.datetime) -> None:
    idle_before = now - dt.timedelta(days=IDLE_DAYS)
    low = (
        person.last_score is not None
        and person.previous_score is not None
        and person.last_score < LOW_SCORE
        and person.previous_score < LOW_SCORE
    )
    if person.last_at is None:
        person.status, person.since = "never", person.created_at
    elif person.ready:
        person.status, person.since = "ready", person.last_scored_at or person.last_at
    elif person.last_at < idle_before:
        person.status, person.since = "idle", person.last_at
    elif low:
        person.status, person.since = "low", person.last_scored_at
    else:
        person.status, person.since = "ok", None


async def _compare_attempts(session, learners, base, where, finished_at, summary_part, now) -> None:
    """Fill each learner's like-for-like history. Only the same job role and difficulty compare."""
    rows = await session.execute(
        sa.select(
            Interview.user_id, Interview.interview_mode, Interview.difficulty, Interview.track,
            JobProfile.job_name, summary_part.label("summary"), Report.overall_score,
        )
        .outerjoin(JobProfile, JobProfile.id == Interview.job_profile_id)
        .outerjoin(SummaryReport, SummaryReport.interview_id == Interview.id)
        .outerjoin(Report, Report.interview_id == Interview.id)
        .where(
            Interview.user_id.in_(base), Interview.status == "completed", where,
            finished_at >= now - dt.timedelta(days=COMPARE_DAYS),
        )
        .order_by(Interview.user_id, finished_at.desc())
    )
    attempts: dict[int, list] = defaultdict(list)
    for row in rows:
        report = SimpleNamespace(overall_score=row.overall_score) if row.overall_score is not None else None
        summary = SimpleNamespace(report_json={"overallScoreSummary": row.summary}) if row.summary else None
        value = _extract_overall_score(report, summary)
        if value is None and row.summary:
            value = readiness.score_of({"overallScoreSummary": row.summary})
        if value is None:
            continue
        part = row.summary if isinstance(row.summary, dict) else {}
        attempts[row.user_id].append(
            SimpleNamespace(
                key=((row.job_name or row.track or "").casefold(), row.difficulty),
                mode=row.interview_mode,
                score=value,
                knowledge=(part.get("knowledgeCompetence") or {}).get("percentage"),
                speech=(part.get("speechAndStructure") or part.get("speechStructure") or {}).get("percentage"),
            )
        )
    for uid, history in attempts.items():
        person = learners[uid]
        same = [a for a in history if a.key == history[0].key]
        if len(same) > 1:
            # "Low twice" and "improving" are only fair between attempts that can be compared.
            person.previous_score = same[1].score
            person.improvement = round(same[0].score - same[-1].score, 1)
        formal = [a.score for a in history if a.mode == "assessment"]
        if formal:
            person.assessment_average = round(sum(formal) / len(formal), 1)
        latest = next((a for a in history if a.knowledge is not None and a.speech is not None), None)
        if latest:
            person.knowledge, person.speech = latest.knowledge, latest.speech


async def _due_work(session, base, course_id, start, now):
    """Assignments whose window closed, and coaching that fell due, in this period."""
    begin = dt.datetime.combine(start, dt.time.min, IST)
    clauses = [
        AssessmentAssignment.student_id.in_(base),
        AssessmentAssignment.status != "cancelled",
        AssessmentAssignment.closes_at.is_not(None),
        AssessmentAssignment.closes_at >= begin,
        AssessmentAssignment.closes_at <= now,
    ]
    if course_id and course_id != NO_COURSE:
        clauses.append(AssessmentAssignment.course_id == course_id)
    rows = (await session.execute(sa.select(AssessmentAssignment).where(*clauses))).scalars().all()
    await settle_assignments(session, rows)
    assigned = [(r.student_id, r.status == "completed") for r in rows]

    clauses = [CoachingAssignment.student_id.in_(base), CoachingAssignment.status != "cancelled"]
    if course_id and course_id != NO_COURSE:
        clauses.append(CoachingAssignment.course_id == course_id)
    coached = []
    today = now.astimezone(IST).date()
    for row in (await session.execute(sa.select(CoachingAssignment).where(*clauses))).scalars():
        due = row.due_date or (row.created_at.astimezone(IST).date() + dt.timedelta(days=DEFAULT_DUE_DAYS))
        if start <= due <= today:
            coached.append((row.student_id, row.status == "completed"))
    return assigned, coached


async def load_context(user: User, session, days: int, course_id: str | None = None) -> Context:
    if days not in PERIODS:
        raise fastapi.HTTPException(422, "Choose a period of 1, 3, 7, 15 or 30 days.")
    viewer = await viewer_for(user, session)
    key = (user.id, days, course_id or "", viewer.lens, viewer.colleges, viewer.grants)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]

    now = dt.datetime.now(UTC)
    scope = _scope_clause(viewer)
    fresh = sa.case((freshness_predicate(), True), else_=False).label("fresh")
    base = sa.select(User.id).where(student_only(), scope)
    rows = (
        await session.execute(
            sa.select(
                User.id, User.name, User.email, User.university, User.enrolled_course_ids,
                User.created_at, fresh,
            ).where(student_only(), scope)
        )
    ).all()

    aliases = await _aliases(session)
    missing = [row.email.strip().lower() for row in rows if not (row.university or "").strip() and row.email]
    imported = (
        dict((await session.execute(
            sa.select(LearnerImportCollege.email, LearnerImportCollege.college)
            .where(LearnerImportCollege.email.in_(missing))
        )).all())
        if missing
        else {}
    )
    spellings: dict[str, Counter] = defaultdict(Counter)
    fixed_labels: dict[str, str] = {}
    learners: dict[int, Learner] = {}
    for row in rows:
        raw = row.university if (row.university or "").strip() else imported.get((row.email or "").strip().lower())
        ckey = college_key(raw)
        if ckey in aliases:
            ckey, label = aliases[ckey]
            fixed_labels[ckey] = label
        elif ckey != NOT_SET:
            spellings[ckey][" ".join(raw.split())] += 1
        learners[row.id] = Learner(
            id=row.id,
            name=row.name or row.email or f"Learner {row.id}",
            email=row.email,
            college_key=ckey,
            college_label="College not set",
            courses=_visible_courses(viewer, ckey, row.enrolled_course_ids) if row.fresh else (),
            created_at=row.created_at,
        )
    labels = {k: c.most_common(1)[0][0] for k, c in spellings.items()} | fixed_labels
    for person in learners.values():
        if person.college_key != NOT_SET:
            person.college_label = labels[person.college_key]

    course_names: dict[str, str] = {}
    if learners:
        where = _interview_course(course_id)
        for uid, at in await session.execute(
            sa.select(Interview.user_id, sa.func.max(Interview.created_at))
            .where(Interview.user_id.in_(base), where)
            .group_by(Interview.user_id)
        ):
            learners[uid].last_at = at
        for uid, at in await session.execute(
            sa.select(PracticeDrillCompletion.user_id, sa.func.max(PracticeDrillCompletion.completed_at))
            .where(PracticeDrillCompletion.user_id.in_(base))
            .group_by(PracticeDrillCompletion.user_id)
        ):
            person = learners[uid]
            person.last_at = max(person.last_at, at) if person.last_at else at

        finished_at = sa.func.coalesce(Interview.completed_at, Interview.created_at)
        ranked = (
            sa.select(
                Interview.id.label("iid"), Interview.user_id.label("uid"), finished_at.label("at"),
                sa.func.row_number().over(partition_by=Interview.user_id, order_by=finished_at.desc()).label("rn"),
            )
            .where(Interview.user_id.in_(base), Interview.status == "completed", where)
            .subquery()
        )
        summary_part = sa.func.coalesce(
            SummaryReport.report_json["overallScoreSummary"], SummaryReport.report_json["scoreSummary"]
        )
        latest: dict[int, list] = defaultdict(list)
        for row in await session.execute(
            sa.select(ranked.c.uid, ranked.c.at, ranked.c.rn, summary_part.label("summary"), Report.overall_score)
            .outerjoin(SummaryReport, SummaryReport.interview_id == ranked.c.iid)
            .outerjoin(Report, Report.interview_id == ranked.c.iid)
            .where(ranked.c.rn <= 6)
            .order_by(ranked.c.uid, ranked.c.rn)
        ):
            report = SimpleNamespace(overall_score=row.overall_score) if row.overall_score is not None else None
            summary = SimpleNamespace(report_json={"overallScoreSummary": row.summary}) if row.summary else None
            value = _extract_overall_score(report, summary)
            if value is None and row.summary:
                value = readiness.score_of({"overallScoreSummary": row.summary})
            if value is not None:
                latest[row.uid].append((value, row.at))
        for uid, values in latest.items():
            person = learners[uid]
            person.last_score, person.last_scored_at = values[0]
            if len(values) > 1:
                person.previous_score = values[1][0]

        await _compare_attempts(session, learners, base, where, finished_at, summary_part, now)
        for uid, n in await session.execute(
            sa.select(Interview.user_id, sa.func.count(Interview.id))
            .where(Interview.user_id.in_(base), Interview.status == "completed", where)
            .group_by(Interview.user_id)
        ):
            learners[uid].interviews = n

        for row in await session.execute(
            sa.select(
                StudentPlacement.student_id, StudentPlacement.course_id, StudentPlacement.batch_key,
                StudentPlacement.batch_name, StudentPlacement.course_name,
            ).where(StudentPlacement.student_id.in_(base))
        ):
            if row.course_name:
                course_names[row.course_id] = row.course_name
            person = learners[row.student_id]
            if row.course_id in person.courses:
                person.batches.append((row.course_id, row.batch_key, row.batch_name, row.course_name))

        by_course: dict[str, list[int]] = defaultdict(list)
        for person in learners.values():
            for c in person.courses:
                if course_id in (None, "", NO_COURSE) or c == course_id:
                    by_course[c].append(person.id)
        for c, ids in by_course.items():
            rule = readiness.rule_view(await readiness.rule_for(session, c))
            found = await readiness.assess(session, c, ids, rule, now)
            for sid, result in found.items():
                if result["status"] == "ready":
                    learners[sid].ready = True

    candidates_at: dict[int, dt.datetime] = {}
    if learners:
        for sid, at in await session.execute(
            sa.select(OpportunityCandidate.student_id, sa.func.max(OpportunityCandidate.added_at))
            .where(OpportunityCandidate.student_id.in_(base))
            .group_by(OpportunityCandidate.student_id)
        ):
            candidates_at[sid] = at
            learners[sid].put_forward = True
    for person in learners.values():
        _classify(person, now)

    today = now.astimezone(IST).date()
    start = today - dt.timedelta(days=days - 1)
    previous_start = start - dt.timedelta(days=days)
    low_at = dt.datetime.combine(previous_start - dt.timedelta(days=1), dt.time.min, UTC)
    interviews = []
    if learners:
        interviews = list(
            (
                await session.execute(
                    sa.select(
                        Interview.id, Interview.user_id, Interview.status, Interview.interview_mode,
                        Interview.created_at,
                    ).where(Interview.user_id.in_(base), Interview.created_at >= low_at, _interview_course(course_id))
                )
            ).all()
        )
    drills = []
    assigned: list[tuple[int, bool]] = []
    coached: list[tuple[int, bool]] = []
    if learners:
        drills = list(
            (
                await session.execute(
                    sa.select(PracticeDrillCompletion.user_id, PracticeDrillCompletion.completed_at).where(
                        PracticeDrillCompletion.user_id.in_(base), PracticeDrillCompletion.completed_at >= low_at,
                    )
                )
            ).all()
        )
        assigned, coached = await _due_work(session, base, course_id, start, now)
    finished = [i.id for i in interviews if i.status == "completed" and i.interview_mode == "assessment"]
    service = AnalyticsService(session)
    reports = await service._reports_by_interview(finished)
    summaries = await service._summary_reports_by_interview(finished)
    scores = {
        iid: value
        for iid in finished
        if (value := _extract_overall_score(reports.get(iid), summaries.get(iid))) is not None
    }
    in_period = lambda at: start <= _ist_day(at) <= today  # noqa: E731
    interviewed_ids = {i.user_id for i in interviews if in_period(i.created_at)}
    active_ids = {i.user_id for i in interviews if in_period(i.created_at)} | {
        d.user_id for d in drills if in_period(d.completed_at)
    }
    assessed_ids = {
        i.user_id for i in interviews
        if i.status == "completed" and i.interview_mode == "assessment" and in_period(i.created_at)
    }
    context = Context(
        viewer, days, list(learners.values()), interviews, scores, today, start, previous_start,
        course_names, candidates_at, drills, assigned, coached, active_ids, interviewed_ids, assessed_ids,
    )
    _cache[key] = (time.monotonic(), context)
    while len(_cache) > CACHE_ENTRIES:
        _cache.popitem(last=False)
    return context


@dataclass(frozen=True)
class Level:
    college: str | None = None
    course: str | None = None
    batch: str | None = None


def effective_level(context: Context, level: Level) -> Level:
    """Staff who cover one college or one course start inside it."""
    if context.viewer.admin:
        return level
    college, course = level.college, level.course
    if not college:
        keys = {p.college_key for p in context.learners}
        if len(keys) == 1:
            college = next(iter(keys))
    if not course:
        ids = {c for p in context.learners for c in p.courses}
        if len(ids) == 1:
            course = next(iter(ids))
    return Level(college, course, level.batch)


def in_level(person: Learner, level: Level) -> bool:
    if level.college and person.college_key != level.college:
        return False
    if level.course:
        if level.course == NO_COURSE:
            if person.courses:
                return False
        elif level.course not in person.courses:
            return False
    if level.batch:
        batches = [b for b in person.batches if not level.course or b[0] == level.course]
        if level.batch == NO_BATCH:
            return not batches
        return any(b[1] == level.batch for b in batches)
    return True


def filter_learners(context: Context, level: Level) -> list[Learner]:
    return [p for p in context.learners if in_level(p, level)]


def _ist_day(at: dt.datetime) -> dt.date:
    return at.astimezone(IST).date()


def share(part: int, whole: int) -> int:
    return round(part / whole * 100) if whole else 0


def period_figures(context: Context, ids: set[int]) -> dict:
    rows = [i for i in context.interviews if i.user_id in ids]
    dates = [context.start + dt.timedelta(days=n) for n in range(context.days)]

    def figures(lo, hi, whole_period=True):
        now_rows = [i for i in rows if lo <= _ist_day(i.created_at) <= hi]
        done = [i for i in now_rows if i.status == "completed" and i.interview_mode == "assessment"]
        values = [context.scores[i.id] for i in done if i.id in context.scores]
        active = {i.user_id for i in now_rows} | {
            d.user_id for d in context.drills if d.user_id in ids and lo <= _ist_day(d.completed_at) <= hi
        }
        enough = len(values) >= (MIN_EVIDENCE if whole_period else 1)
        return {
            "active": len(active),
            "assessments": len(done),
            "average_score": round(sum(values) / len(values), 1) if values and enough else None,
            "scored": len(values),
            "put_forward": sum(
                1 for uid, at in context.candidates_at.items() if uid in ids and lo <= _ist_day(at) <= hi
            ),
        }

    current = figures(context.start, context.today)
    previous = figures(context.previous_start, context.start - dt.timedelta(days=1))
    daily = [figures(d, d, whole_period=False) for d in dates]

    def metric(name):
        value, before = current[name], previous[name]
        return {
            "value": value,
            "previous": before,
            "change": None if value is None or before is None else round(value - before, 1),
            "series": [d[name] for d in daily],
        }

    return {"metrics": {name: metric(name) for name in current if name != "scored"}, "current": current}


def actions_for(context: Context, people: list[Learner]) -> list[dict]:
    viewer, now, total = context.viewer, dt.datetime.now(UTC), len(people)

    def owner(kind):
        if kind == "program":
            return "You" if viewer.lens == "program" and not viewer.lead else "Facilitators"
        return "You" if viewer.lens == "placement" else "Placement"

    def item(key, title, group, status, cta, kind):
        waited = [(now - p.since).days for p in group if p.since]
        oldest = max(waited, default=0)
        return {
            "id": key, "title": title, "count": len(group), "share": share(len(group), total),
            "oldest_days": oldest, "overdue": oldest >= OVERDUE_DAYS,
            "next_owner": owner(kind), "status": status, "cta": cta,
        }

    never = [p for p in people if p.status == "never"]
    idle = [p for p in people if p.status == "idle"]
    low = [p for p in people if p.status == "low"]
    fresh_ready = [p for p in people if p.status == "ready" and not p.put_forward]
    stale = [p for p in people if p.stale]
    program = [
        item("never", "Joined but never started", never, "never", "nudge", "program"),
        item("idle", "No activity in 7 days", idle, "idle", "coaching", "program"),
        item("low", "Low score twice in a row", low, "low", "coaching", "program"),
    ]
    placement = [
        item("ready", "Ready but not put forward", fresh_ready, "ready_open", "opening", "placement"),
        item("stale", "Evidence is getting old", stale, "stale", "assessment", "placement"),
    ]
    chosen = (
        placement if viewer.lens == "placement" else program + placement[:1] if viewer.admin else program
    )
    return sorted([a for a in chosen if a["count"]], key=lambda a: -a["oldest_days"])


def funnel(people: list[Learner]) -> list[dict]:
    total = len(people)
    steps = [
        ("Joined", "all", total),
        ("Started practising", "started", sum(1 for p in people if p.last_at)),
        ("Have a score", "scored", sum(1 for p in people if p.last_score is not None)),
        ("Ready to put forward", "reached_ready", sum(1 for p in people if p.status == "ready" or p.put_forward)),
        ("Put forward", "put_forward", sum(1 for p in people if p.put_forward)),
    ]
    return [{"label": label, "status": key, "count": n, "share": share(n, total)} for label, key, n in steps]


def _ratio(done: int, total: int) -> int | None:
    """A percentage only once enough people stand behind it."""
    return share(done, total) if total >= MIN_EVIDENCE else None


def outcomes(context: Context, people: list[Learner], figures: dict) -> dict:
    """The figures that say whether learners are progressing. Every one links to its people."""
    ids = {p.id for p in people}
    assigned = [done for sid, done in context.assignments if sid in ids]
    coached = [done for sid, done in context.coaching if sid in ids]
    compared = [p for p in people if p.improvement is not None]
    improving = sum(1 for p in compared if p.improvement >= IMPROVED_POINTS)
    scored = [p for p in people if p.last_score is not None]
    low = sum(1 for p in scored if p.last_score < LOW_SCORE)
    high = sum(1 for p in scored if p.last_score >= HIGH_SCORE)
    bands = [
        ("Below 60", "band_low", low),
        ("60 to 74", "band_mid", len(scored) - low - high),
        ("75 and above", "band_high", high),
    ]
    parts = [p for p in people if p.knowledge is not None and p.speech is not None]
    enough = len(parts) >= MIN_EVIDENCE
    return {
        "min_evidence": MIN_EVIDENCE,
        "scored_attempts": figures["scored"],
        "active_share": share(figures["active"], len(people)),
        "assignments": {"total": len(assigned), "finished": sum(assigned), "share": _ratio(sum(assigned), len(assigned))},
        "coaching": {"total": len(coached), "finished": sum(coached), "share": _ratio(sum(coached), len(coached))},
        "improving": {"compared": len(compared), "improving": improving, "share": _ratio(improving, len(compared))},
        "bands": [
            {"label": label, "status": key, "count": n, "share": share(n, len(scored))} for label, key, n in bands
        ],
        "scored_learners": len(scored),
        "skills": {
            "learners": len(parts),
            "knowledge": round(sum(p.knowledge for p in parts) / len(parts)) if enough else None,
            "speech": round(sum(p.speech for p in parts) / len(parts)) if enough else None,
        },
        "ready": sum(1 for p in people if p.status == "ready"),
        "stale": sum(1 for p in people if p.stale),
    }


async def weak_skills(session, people: list[Learner]) -> list[dict]:
    """The most common improvement themes across this group's latest answers."""
    if not people:
        return []
    ids = [p.id for p in people]
    since = dt.datetime.now(UTC) - dt.timedelta(days=90)
    counts: Counter = Counter()
    per_learner: dict[str, set] = defaultdict(set)
    rows = await session.execute(
        sa.select(Interview.user_id, QuestionAttempt.analysis_json)
        .join(Interview, Interview.id == QuestionAttempt.interview_id)
        .where(Interview.user_id.in_(ids), Interview.created_at >= since)
        .limit(20000)
    )
    for uid, analysis in rows:
        if not isinstance(analysis, dict):
            continue
        for part in ("communication", "domain"):
            section = analysis.get(part) or {}
            improvements = section.get("improvements") if isinstance(section, dict) else None
            if not isinstance(improvements, list):
                continue
            for tag in improvements:
                if isinstance(tag, str) and tag.strip():
                    clean = " ".join(tag.split())
                    counts[clean.casefold()] += 1
                    per_learner[clean.casefold()].add(uid)
    display = sorted(per_learner, key=lambda t: (-len(per_learner[t]), -counts[t]))[:5]
    return [
        {"label": t[:1].upper() + t[1:], "learners": len(per_learner[t]), "share": share(len(per_learner[t]), len(people))}
        for t in display
    ]


def group_rows(context: Context, of: str, level: Level, q: str = "") -> tuple[list[dict], int]:
    people = filter_learners(context, level)
    buckets: dict[str, list[Learner]] = defaultdict(list)
    info: dict[str, dict] = {}
    no_batch = 0
    for person in people:
        if of == "colleges":
            buckets[person.college_key].append(person)
            info[person.college_key] = {"key": person.college_key, "label": person.college_label}
        elif of == "courses":
            if not person.courses:
                buckets[NO_COURSE].append(person)
                info[NO_COURSE] = {"key": NO_COURSE, "label": "Not in a course"}
            for c in person.courses:
                if level.course and level.course != c:
                    continue
                buckets[c].append(person)
                info[c] = {"key": c, "label": context.course_names.get(c, c)}
        else:
            batches = [b for b in person.batches if not level.course or b[0] == level.course]
            if not batches:
                no_batch += 1
            for course_id, batch_key, batch_name, course_name in batches:
                k = f"{course_id}|{batch_key}"
                buckets[k].append(person)
                info[k] = {
                    "key": batch_key, "label": batch_name, "course_id": course_id,
                    "course_name": course_name or context.course_names.get(course_id, course_id),
                }
    query = q.strip().casefold()
    out = []
    for key, group in buckets.items():
        meta = info[key]
        if query and query not in f"{meta['label']} {meta.get('course_name', '')}".casefold():
            continue
        ids = {p.id for p in group}
        figures = period_figures(context, ids)["current"]
        need = sum(1 for p in group if p.status in NEEDS_ACTION)
        ready = sum(1 for p in group if p.status == "ready")
        batch_keys = {(b[0], b[1]) for p in group for b in p.batches}
        out.append(
            meta
            | {
                "learners": len(group), "active": figures["active"], "active_share": share(figures["active"], len(group)),
                "assessments": figures["assessments"], "average_score": figures["average_score"],
                "need_action": need, "need_share": share(need, len(group)),
                "ready": ready, "ready_share": share(ready, len(group)),
                "colleges": len({p.college_key for p in group if p.college_key != NOT_SET}),
                "courses": len({c for p in group for c in p.courses}),
                "batches": len(batch_keys),
            }
        )
    placement = context.viewer.lens == "placement"
    out.sort(key=lambda r: (-(r["ready_share"] if placement else r["need_share"]), r["label"].casefold()))
    return out, no_batch


def status_filters(context: Context) -> dict:
    missed = {sid for sid, done in context.assignments if not done}
    overdue = {sid for sid, done in context.coaching if not done}
    return {
        "all": lambda p: True,
        "need": lambda p: p.status in NEEDS_ACTION,
        "ready": lambda p: p.status == "ready",
        "ready_open": lambda p: p.status == "ready" and not p.put_forward,
        "stale": lambda p: p.stale,
        "ok": lambda p: p.status == "ok",
        "never": lambda p: p.status == "never",
        "idle": lambda p: p.status == "idle",
        "low": lambda p: p.status == "low",
        "active": lambda p: p.id in context.active_ids,
        "inactive": lambda p: p.id not in context.active_ids,
        "assessed": lambda p: p.id in context.assessed_ids,
        # The two lists behind the Overview's first two numbers.
        "signed_up": lambda p: p.created_at is not None
        and context.start <= _ist_day(p.created_at) <= context.today,
        "interviewed": lambda p: p.id in context.interviewed_ids,
        "started": lambda p: p.last_at is not None,
        "scored": lambda p: p.last_score is not None,
        "reached_ready": lambda p: p.status == "ready" or p.put_forward,
        "put_forward": lambda p: p.put_forward,
        "put_forward_period": lambda p: p.id in context.candidates_at
        and context.start <= _ist_day(context.candidates_at[p.id]) <= context.today,
        "band_low": lambda p: p.last_score is not None and p.last_score < LOW_SCORE,
        "band_mid": lambda p: p.last_score is not None and LOW_SCORE <= p.last_score < HIGH_SCORE,
        "band_high": lambda p: p.last_score is not None and p.last_score >= HIGH_SCORE,
        "improving": lambda p: p.improvement is not None and p.improvement >= IMPROVED_POINTS,
        "not_improving": lambda p: p.improvement is not None and p.improvement < IMPROVED_POINTS,
        "missed_assignment": lambda p: p.id in missed,
        "coaching_overdue": lambda p: p.id in overdue,
    }


def match_learners(
    context: Context, level: Level, *, q: str = "", status: str = "all",
    college: str = "", course: str = "", batch: str = "", ignore: str = "",
) -> list[Learner]:
    sub = Level(
        level.college or (college if ignore != "college" else None) or None,
        level.course or (course if ignore != "course" else None) or None,
        level.batch or (batch if ignore != "batch" else None) or None,
    )
    people = filter_learners(context, sub)
    query = q.strip().casefold()
    if query:
        people = [
            p for p in people
            if query in f"{p.name} {p.email or ''} {p.college_label}".casefold()
        ]
    predicates = status_filters(context)
    if status not in predicates:
        raise fastapi.HTTPException(422, "Unknown learner status filter.")
    return [p for p in people if predicates[status](p)]


def sort_learners(context: Context, people: list[Learner], sort: str) -> list[Learner]:
    far = dt.datetime.min.replace(tzinfo=UTC)
    if sort == "name":
        return sorted(people, key=lambda p: p.name.casefold())
    if sort == "score":
        return sorted(people, key=lambda p: (p.last_score is None, p.last_score or 0, p.name.casefold()))
    if sort == "recent":
        return sorted(people, key=lambda p: (p.last_at is None, -(p.last_at or far).timestamp()))
    if sort != "urgent":
        raise fastapi.HTTPException(422, "Unknown sort.")
    if context.viewer.lens == "placement":
        return sorted(people, key=lambda p: (p.status != "ready", p.put_forward, p.name.casefold()))
    return sorted(
        people,
        key=lambda p: (URGENCY[p.status], (p.since or dt.datetime.now(UTC)).timestamp(), p.name.casefold()),
    )


def learner_row(context: Context, person: Learner, level: Level) -> dict:
    now = dt.datetime.now(UTC)
    batches = [b for b in person.batches if not level.course or b[0] == level.course]
    return {
        "id": person.id,
        "name": person.name,
        "college": person.college_label,
        "college_key": person.college_key,
        "courses": [{"id": c, "name": context.course_names.get(c, c)} for c in person.courses],
        "batch": batches[0][2] if batches else None,
        "last_active_at": person.last_at,
        "last_active_days": (now - person.last_at).days if person.last_at else None,
        "latest_score": round(person.last_score, 1) if person.last_score is not None else None,
        "assessment_average": person.assessment_average,
        "improvement": person.improvement,
        "interviews": person.interviews,
        "status": person.status,
        "status_label": STATUS_LABELS[person.status],
        "put_forward": person.put_forward,
    }


def capabilities(viewer: Viewer) -> dict:
    user, policy = viewer.user, viewer.policy
    has = bool(viewer.grants)
    def allowed(feature, op):
        return policy is None or permits(policy, feature, op)
    explicit = lambda feature, op: policy is not None and permits(policy, feature, op)  # noqa: E731
    return {
        "add_learners": viewer.admin or viewer.lead,
        "set_college": viewer.admin or viewer.lead,
        "merge_colleges": viewer.admin,
        "export": viewer.admin or explicit("course_workspace", "export"),
        "coach": viewer.admin or (has and viewer.lens == "program" and allowed("practice_coaching", "create")),
        "open_opening": viewer.admin or (has and explicit("placement_shortlists", "update")),
        "assign_assessment": viewer.admin or (has and explicit("assessment_assignments", "create")),
        "edit_rules": viewer.admin or explicit("course_workspace", "update") or explicit("placement_shortlists", "update"),
        "is_admin": viewer.admin,
    }
