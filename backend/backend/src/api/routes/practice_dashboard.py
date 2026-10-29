"""Purpose-limited staff dashboards for the Practice interview programme.

The route intentionally returns derived coaching/readiness evidence only. Raw
audio URLs, transcripts, prompt bodies, and report JSON are never serialized.
Every individual-student read is checked for role, college, purpose and current
consent on the server; hiding a tab in the frontend is not an authorization
control.
"""

from __future__ import annotations

import datetime
from typing import Any, Literal

import fastapi
import pydantic
import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.admin import (
    calculate_user_permissions,
    get_user_active_memberships,
    is_admin_user,
    is_bulk_ats_user,
    is_super_admin_user,
    normalize_role,
)
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.ai_job import AIJob
from src.models.db.interview import Interview
from src.models.db.practice_drill_completion import PracticeDrillCompletion
from src.models.db.practice_governance import (
    PracticeAssignment,
    PracticeConsent,
    PracticeDashboardAuditEvent,
    PracticeFeatureControl,
    PracticeShortlist,
    PracticeShortlistEntry,
)
from src.models.db.question_attempt import QuestionAttempt
from src.models.db.summary_report import SummaryReport
from src.models.db.team_member import TeamMember
from src.models.db.user import User

router = fastapi.APIRouter(prefix="/v2/practice-dashboard", tags=["practice-dashboard"])

DashboardRole = Literal["facilitator", "placements", "admin", "super_admin"]
ConsentPurpose = Literal["facilitator_coaching", "opportunity_readiness"]
DrillModule = Literal["structure", "pronunciation", "pace", "warm_up", "think_fast"]

_FACILITATOR_ROLES = {
    "facilitator",
    "jr facilitator",
    "junior facilitator",
    "sr facilitator",
    "senior facilitator",
    "creator",
    "program manager",
    "lead",
    "team lead",
}
_PLACEMENT_ROLES = {"recruiter", "placement staff", "placement com", "partnership staff"}
_CONTROL_KEYS = {"practice_new_starts", "question_generation", "report_generation"}


class ConsentUpdate(pydantic.BaseModel):
    purpose: ConsentPurpose
    granted: bool
    policy_version: str = pydantic.Field(min_length=1, max_length=32)
    expires_at: datetime.datetime | None = None


class AssignmentCreate(pydantic.BaseModel):
    module: DrillModule
    reason: str = pydantic.Field(min_length=10, max_length=1000)
    note: str | None = pydantic.Field(default=None, max_length=2000)
    due_date: datetime.date | None = None


class ShortlistCreate(pydantic.BaseModel):
    name: str = pydantic.Field(min_length=3, max_length=160)
    opportunity: str = pydantic.Field(min_length=3, max_length=200)
    role: str = pydantic.Field(min_length=2, max_length=100)
    maximum_evidence_age_days: int = pydantic.Field(default=90, ge=1, le=365)
    minimum_completed_interviews: int = pydantic.Field(default=1, ge=1, le=20)
    expires_at: datetime.datetime


class ShortlistDecision(pydantic.BaseModel):
    student_id: int
    decision: Literal["include", "exclude"]
    reason: str = pydantic.Field(min_length=10, max_length=1000)


class ReasonBody(pydantic.BaseModel):
    reason: str = pydantic.Field(min_length=10, max_length=1000)


class ControlUpdate(ReasonBody):
    enabled: bool


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _membership_parts(item: Any) -> tuple[Any, Any]:
    if isinstance(item, (tuple, list)):
        return item[0], item[1] if len(item) > 1 else None
    try:
        return item[0], item[1]
    except (KeyError, IndexError, TypeError):
        return item, getattr(item, "team", None)


def resolve_dashboard_role(user: User, memberships: list[Any], *, preferred: str | None = None) -> DashboardRole | None:
    """Resolve the narrowest dashboard persona from trusted server-side data."""

    if is_super_admin_user(user):
        return "super_admin"
    if is_admin_user(user):
        return "admin"
    token_role = normalize_role(
        getattr(user, "_token_role", "") or getattr(user, "role", "")
    )
    if not memberships and getattr(user, "_token_source", None) is None and (token_role in _PLACEMENT_ROLES or is_bulk_ats_user(user)):
        return "placements"
    if not memberships and getattr(user, "_token_source", None) is None and token_role in _FACILITATOR_ROLES:
        return "facilitator"
    for item in memberships:
        member, team = _membership_parts(item)
        team_name = (getattr(team, "name", "") or "").casefold()
        role = normalize_role(getattr(member, "access_role", ""))
        raw_purposes = getattr(member, "purposes", None)
        domain = getattr(member, "domain", None)
        purposes = {value.strip().casefold() for value in raw_purposes if isinstance(value, str)} if isinstance(raw_purposes, list) else set()
        if isinstance(domain, str) and domain.strip():
            purposes.add(domain.strip().casefold())
        # Explicit assigned responsibility precedes a display name. Course and
        # college clauses remain paired with this specific membership.
        placement = bool(purposes & {"placement", "placements", "partnership"})
        coaching = bool(purposes & {"program", "programme", "coaching"})
        if preferred == "facilitator" and coaching:
            return "facilitator"
        if placement:
            return "placements"
        if coaching:
            return "facilitator"
        if role in _PLACEMENT_ROLES:
            return "placements"
        if not purposes and any(word in team_name for word in ("placement", "partnership")):
            return "placements"
        if role in _FACILITATOR_ROLES:
            return "facilitator"
    return None


async def _context(
    user: User, session: AsyncSession
) -> tuple[DashboardRole, set[str] | None]:
    memberships = await get_user_active_memberships(user.id, session)
    role = resolve_dashboard_role(user, memberships)
    if role is None:
        raise fastapi.HTTPException(
            status_code=403, detail="Practice staff access required"
        )
    # These older aggregations do not bind evidence to a course. Course-limited
    # staff must use the course workspace until each workflow is migrated.
    from src.services.course_access import require_global_legacy_analytics

    await require_global_legacy_analytics(user, session)
    permissions = calculate_user_permissions(user, memberships)
    colleges = permissions.get("colleges")
    return role, (
        None if colleges is None else {str(item).casefold() for item in colleges}
    )


def _capabilities(role: DashboardRole) -> list[str]:
    return {
        "facilitator": ["overview", "student_coaching", "assign_drill"],
        "placements": ["overview", "readiness", "manage_shortlists"],
        "admin": ["overview", "operations", "ai_quality", "feature_controls"],
        "super_admin": ["overview", "ai_quality", "access_audit", "feature_controls"],
    }[role]


def _purpose(role: DashboardRole) -> ConsentPurpose | None:
    if role == "facilitator":
        return "facilitator_coaching"
    if role == "placements":
        return "opportunity_readiness"
    return None


def _consent_is_active(consent: PracticeConsent | None, now: datetime.datetime) -> bool:
    return bool(
        consent
        and consent.granted
        and consent.withdrawn_at is None
        and (consent.expires_at is None or consent.expires_at > now)
    )


async def _active_consent(
    session: AsyncSession, student_id: int, purpose: ConsentPurpose
) -> PracticeConsent | None:
    stmt = sqlalchemy.select(PracticeConsent).where(
        PracticeConsent.user_id == student_id,
        PracticeConsent.purpose == purpose,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _college_allowed(student: User, allowed: set[str] | None) -> bool:
    return allowed is None or (student.university or "").strip().casefold() in allowed


async def _student_or_404(session: AsyncSession, student_id: int) -> User:
    student = await session.get(User, student_id)
    membership_exists = bool(
        (
            await session.execute(
                sqlalchemy.select(TeamMember.id)
                .where(TeamMember.user_id == student_id, TeamMember.status == "active")
                .limit(1)
            )
        ).scalar_one_or_none()
    )
    if (
        student is None
        or is_admin_user(student)
        or student.role is not None
        or membership_exists
    ):
        raise fastapi.HTTPException(status_code=404, detail="Student not found")
    return student


async def _authorize_student(
    *,
    session: AsyncSession,
    role: DashboardRole,
    allowed_colleges: set[str] | None,
    student_id: int,
) -> tuple[User, PracticeConsent | None]:
    if role in {"admin", "super_admin"}:
        raise fastapi.HTTPException(
            status_code=403,
            detail="This role receives operational or policy metadata, not student coaching evidence",
        )
    student = await _student_or_404(session, student_id)
    if not _college_allowed(student, allowed_colleges):
        # Avoid confirming that an out-of-scope student exists.
        raise fastapi.HTTPException(status_code=404, detail="Student not found")
    purpose = _purpose(role)
    consent = await _active_consent(session, student_id, purpose) if purpose else None
    if purpose and not _consent_is_active(consent, _utcnow()):
        raise fastapi.HTTPException(
            status_code=403,
            detail=f"Current student consent for {purpose.replace('_', ' ')} is required",
        )
    return student, consent


def _score(report_json: Any) -> float | None:
    if not isinstance(report_json, dict):
        return None
    summary = (
        report_json.get("overallScoreSummary") or report_json.get("scoreSummary") or {}
    )
    for key in ("overallPercentage", "overallScore", "percentage"):
        value = summary.get(key) if isinstance(summary, dict) else None
        if isinstance(value, (int, float)):
            return round(float(value), 2)
    return None


def _feedback_summary(report_json: Any) -> str | None:
    if not isinstance(report_json, dict):
        return None
    for key in ("executiveSummary", "summary", "overallSummary"):
        value = report_json.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    overall = report_json.get("overallFeedback")
    if isinstance(overall, dict):
        for value in overall.values():
            if isinstance(value, str) and value.strip():
                return value.strip()[:500]
            if isinstance(value, dict):
                text = value.get("summary") or value.get("feedback")
                if isinstance(text, str) and text.strip():
                    return text.strip()[:500]
    return None


async def _evidence_snapshot(
    session: AsyncSession, student_id: int, *, include_score: bool = True
) -> dict[str, Any]:
    interviews = list(
        (
            await session.execute(
                sqlalchemy.select(Interview)
                .where(
                    Interview.user_id == student_id,
                    Interview.interview_mode == "practice",
                )
                .order_by(Interview.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    completed = [item for item in interviews if item.status == "completed"]
    latest = completed[0] if completed else None
    report = None
    if latest:
        report = (
            await session.execute(
                sqlalchemy.select(SummaryReport).where(
                    SummaryReport.interview_id == latest.id
                )
            )
        ).scalar_one_or_none()
    drill_count = int(
        (
            await session.execute(
                sqlalchemy.select(
                    sqlalchemy.func.count(PracticeDrillCompletion.id)
                ).where(PracticeDrillCompletion.user_id == student_id)
            )
        ).scalar()
        or 0
    )
    evidence_state = (
        "available" if report and _score(report.report_json) is not None else "missing"
    )
    return {
        "evidence_state": evidence_state,
        "completed_interviews": len(completed),
        "resumable_interviews": sum(
            1 for item in interviews if item.status in {"active", "paused"}
        ),
        "latest_interview_at": latest.completed_at if latest else None,
        "latest_track": latest.track if latest else None,
        "latest_score": (
            _score(report.report_json) if report and include_score else None
        ),
        "latest_feedback": _feedback_summary(report.report_json) if report else None,
        "completed_drills": drill_count,
    }


async def _audit(
    session: AsyncSession,
    *,
    actor_id: int,
    action: str,
    purpose: str,
    target_type: str,
    target_id: str | int,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=actor_id,
            action=action,
            purpose=purpose,
            target_type=target_type,
            target_id=str(target_id),
            reason=reason,
            event_metadata=metadata or {},
        )
    )


@router.get("/me")
async def dashboard_me(
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> dict[str, Any]:
    role, colleges = await _context(current_user, session)
    return {
        "role": role,
        "capabilities": _capabilities(role),
        "assigned_colleges": sorted(colleges) if colleges is not None else None,
        "privacy": {
            "raw_audio": False,
            "transcripts": False,
            "purpose_limited": True,
            "default_deny": True,
        },
    }


@router.get("/consents/me")
async def my_consents(
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> list[dict[str, Any]]:
    rows = list(
        (
            await session.execute(
                sqlalchemy.select(PracticeConsent).where(
                    PracticeConsent.user_id == current_user.id
                )
            )
        )
        .scalars()
        .all()
    )
    by_purpose = {row.purpose: row for row in rows}
    now = _utcnow()
    return [
        {
            "purpose": purpose,
            "granted": _consent_is_active(by_purpose.get(purpose), now),
            "policy_version": getattr(by_purpose.get(purpose), "policy_version", None),
            "expires_at": getattr(by_purpose.get(purpose), "expires_at", None),
            "updated_at": getattr(by_purpose.get(purpose), "updated_at", None),
        }
        for purpose in ("facilitator_coaching", "opportunity_readiness")
    ]


@router.put("/consents/me")
async def update_my_consent(
    body: ConsentUpdate,
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> dict[str, Any]:
    now = _utcnow()
    if body.granted and body.expires_at and body.expires_at <= now:
        raise fastapi.HTTPException(
            status_code=400, detail="Consent expiry must be in the future"
        )
    consent = await _active_consent(session, current_user.id, body.purpose)
    if consent is None:
        consent = PracticeConsent(user_id=current_user.id, purpose=body.purpose)
        session.add(consent)
    consent.granted = body.granted
    consent.policy_version = body.policy_version
    consent.expires_at = body.expires_at if body.granted else None
    consent.granted_at = now if body.granted else None
    consent.withdrawn_at = None if body.granted else now
    await session.commit()
    return {"purpose": body.purpose, "granted": body.granted, "updated_at": now}


@router.get("/overview")
async def overview(
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> dict[str, Any]:
    role, colleges = await _context(current_user, session)
    staff_ids = sqlalchemy.select(TeamMember.user_id).where(
        TeamMember.status == "active"
    )
    student_conditions = [
        User.is_admin.is_(False),
        User.is_super_admin.is_(False),
        User.role.is_(None),
        User.id.not_in(staff_ids),
    ]
    if colleges is not None:
        student_conditions.append(sqlalchemy.func.lower(User.university).in_(colleges))
    student_ids = list(
        (await session.execute(sqlalchemy.select(User.id).where(*student_conditions)))
        .scalars()
        .all()
    )
    if not student_ids:
        return {
            "role": role,
            "students": 0,
            "completed": 0,
            "resumable": 0,
            "blocked": 0,
            "reports_delayed": 0,
        }
    interviews = list(
        (
            await session.execute(
                sqlalchemy.select(Interview).where(
                    Interview.user_id.in_(student_ids),
                    Interview.interview_mode == "practice",
                )
            )
        )
        .scalars()
        .all()
    )
    interview_ids = [item.id for item in interviews]
    report_ids = set(
        (
            await session.execute(
                sqlalchemy.select(SummaryReport.interview_id).where(
                    SummaryReport.interview_id.in_(interview_ids or [-1])
                )
            )
        )
        .scalars()
        .all()
    )
    delayed_cutoff = _utcnow() - datetime.timedelta(minutes=15)
    return {
        "role": role,
        "students": len(student_ids),
        "completed": sum(1 for item in interviews if item.status == "completed"),
        "resumable": sum(
            1 for item in interviews if item.status in {"active", "paused"}
        ),
        "blocked": sum(
            1
            for item in interviews
            if item.status in {"failed", "terminated"} or item.proctoring_terminated
        ),
        "reports_delayed": sum(
            1
            for item in interviews
            if item.status == "completed"
            and item.id not in report_ids
            and (item.completed_at or item.created_at) < delayed_cutoff
        ),
    }


@router.get("/students")
async def students(
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> list[dict[str, Any]]:
    role, colleges = await _context(current_user, session)
    if role not in {"facilitator", "placements"}:
        raise fastapi.HTTPException(
            status_code=403,
            detail="Student evidence is outside this role's approved purpose",
        )
    staff_ids = sqlalchemy.select(TeamMember.user_id).where(
        TeamMember.status == "active"
    )
    conditions = [
        User.is_admin.is_(False),
        User.is_super_admin.is_(False),
        User.role.is_(None),
        User.id.not_in(staff_ids),
    ]
    if colleges is not None:
        conditions.append(sqlalchemy.func.lower(User.university).in_(colleges))
    rows = list(
        (
            await session.execute(
                sqlalchemy.select(User).where(*conditions).order_by(User.name)
            )
        )
        .scalars()
        .all()
    )
    purpose = _purpose(role)
    output: list[dict[str, Any]] = []
    for student in rows:
        consent = (
            await _active_consent(session, student.id, purpose) if purpose else None
        )
        if purpose and not _consent_is_active(consent, _utcnow()):
            continue
        evidence = await _evidence_snapshot(
            session, student.id, include_score=role == "facilitator"
        )
        output.append(
            {
                "id": student.id,
                "name": student.name,
                "college": student.university,
                "consent": "active" if purpose else "operational_access",
                "consent_expires_at": consent.expires_at if consent else None,
                **evidence,
            }
        )
    return output


@router.get("/students/{student_id}")
async def student_detail(
    student_id: int,
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> dict[str, Any]:
    role, colleges = await _context(current_user, session)
    student, consent = await _authorize_student(
        session=session, role=role, allowed_colleges=colleges, student_id=student_id
    )
    assignments = list(
        (
            await session.execute(
                sqlalchemy.select(PracticeAssignment)
                .where(PracticeAssignment.student_id == student_id)
                .order_by(PracticeAssignment.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    await _audit(
        session,
        actor_id=current_user.id,
        action="student_evidence_viewed",
        purpose=_purpose(role) or "operations",
        target_type="student",
        target_id=student_id,
        reason="Role dashboard detail view",
    )
    await session.commit()
    return {
        "student": {
            "id": student.id,
            "name": student.name,
            "college": student.university,
        },
        "consent": {
            "status": "active" if consent else "operational_access",
            "purpose": _purpose(role),
            "expires_at": consent.expires_at if consent else None,
        },
        "evidence": await _evidence_snapshot(session, student_id),
        "assignments": [
            {
                "id": row.id,
                "module": row.module,
                "reason": row.reason,
                "note": row.note,
                "due_date": row.due_date,
                "status": row.status,
                "assigned_by": row.assigned_by,
                "created_at": row.created_at,
            }
            for row in assignments
        ],
        "raw_audio_available": False,
        "transcript_available": False,
    }


@router.post("/students/{student_id}/assignments", status_code=201)
async def assign_drill(
    student_id: int,
    body: AssignmentCreate,
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> dict[str, Any]:
    role, colleges = await _context(current_user, session)
    if role != "facilitator":
        raise fastapi.HTTPException(
            status_code=403, detail="Facilitator coaching permission required"
        )
    await _authorize_student(
        session=session, role=role, allowed_colleges=colleges, student_id=student_id
    )
    if body.due_date and body.due_date < _utcnow().date():
        raise fastapi.HTTPException(
            status_code=400, detail="Due date cannot be in the past"
        )
    assignment = PracticeAssignment(
        student_id=student_id,
        assigned_by=current_user.id,
        module=body.module,
        reason=body.reason.strip(),
        note=body.note.strip() if body.note else None,
        due_date=body.due_date,
    )
    session.add(assignment)
    await session.flush()
    await _audit(
        session,
        actor_id=current_user.id,
        action="drill_assigned",
        purpose="facilitator_coaching",
        target_type="assignment",
        target_id=assignment.id,
        reason=body.reason,
        metadata={"student_id": student_id, "module": body.module},
    )
    await session.commit()
    return {"id": assignment.id, "status": assignment.status}


@router.get("/shortlists")
async def list_shortlists(
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> list[dict[str, Any]]:
    role, _ = await _context(current_user, session)
    if role not in {"placements", "super_admin"}:
        raise fastapi.HTTPException(
            status_code=403, detail="Placement readiness permission required"
        )
    rows = list(
        (
            await session.execute(
                sqlalchemy.select(PracticeShortlist).order_by(
                    PracticeShortlist.created_at.desc()
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": row.id,
            "name": row.name,
            "opportunity": row.opportunity,
            "status": row.status,
            "version": row.version,
            "expires_at": row.expires_at,
        }
        for row in rows
    ]


@router.post("/shortlists", status_code=201)
async def create_shortlist(
    body: ShortlistCreate,
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> dict[str, Any]:
    role, _ = await _context(current_user, session)
    if role != "placements":
        raise fastapi.HTTPException(
            status_code=403, detail="Placement team permission required"
        )
    if body.expires_at <= _utcnow():
        raise fastapi.HTTPException(
            status_code=400, detail="Shortlist expiry must be in the future"
        )
    shortlist = PracticeShortlist(
        name=body.name.strip(),
        opportunity=body.opportunity.strip(),
        evidence_rules={
            "role": body.role.strip(),
            "maximum_evidence_age_days": body.maximum_evidence_age_days,
            "minimum_completed_interviews": body.minimum_completed_interviews,
            "decision_mode": "human_only",
        },
        created_by=current_user.id,
        expires_at=body.expires_at,
    )
    session.add(shortlist)
    await session.flush()
    await _audit(
        session,
        actor_id=current_user.id,
        action="shortlist_created",
        purpose="opportunity_readiness",
        target_type="shortlist",
        target_id=shortlist.id,
        reason=f"Created for {body.opportunity}",
    )
    await session.commit()
    return {
        "id": shortlist.id,
        "status": shortlist.status,
        "version": shortlist.version,
    }


@router.get("/shortlists/{shortlist_id}")
async def shortlist_detail(
    shortlist_id: int,
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> dict[str, Any]:
    role, colleges = await _context(current_user, session)
    if role != "placements":
        raise fastapi.HTTPException(
            status_code=403, detail="Placement readiness permission required"
        )
    shortlist = await session.get(PracticeShortlist, shortlist_id)
    if shortlist is None:
        raise fastapi.HTTPException(status_code=404, detail="Shortlist not found")
    entries = list(
        (
            await session.execute(
                sqlalchemy.select(PracticeShortlistEntry).where(
                    PracticeShortlistEntry.shortlist_id == shortlist_id
                )
            )
        )
        .scalars()
        .all()
    )
    consented_ids = list(
        (
            await session.execute(
                sqlalchemy.select(PracticeConsent.user_id).where(
                    PracticeConsent.purpose == "opportunity_readiness",
                    PracticeConsent.granted.is_(True),
                    PracticeConsent.withdrawn_at.is_(None),
                    sqlalchemy.or_(
                        PracticeConsent.expires_at.is_(None),
                        PracticeConsent.expires_at > _utcnow(),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    users_stmt = sqlalchemy.select(User).where(User.id.in_(consented_ids or [-1]))
    if colleges is not None:
        users_stmt = users_stmt.where(
            sqlalchemy.func.lower(User.university).in_(colleges)
        )
    candidates = list(
        (await session.execute(users_stmt.order_by(User.name))).scalars().all()
    )
    decisions = {entry.student_id: entry for entry in entries}
    candidate_rows = []
    for student in candidates:
        evidence = await _evidence_snapshot(session, student.id, include_score=False)
        entry = decisions.get(student.id)
        candidate_rows.append(
            {
                "student": {
                    "id": student.id,
                    "name": student.name,
                    "college": student.university,
                },
                "evidence": evidence,
                "decision": entry.decision if entry else None,
                "decision_reason": entry.reason if entry else None,
            }
        )
    return {
        "shortlist": {
            "id": shortlist.id,
            "name": shortlist.name,
            "opportunity": shortlist.opportunity,
            "status": shortlist.status,
            "version": shortlist.version,
            "evidence_rules": shortlist.evidence_rules,
            "expires_at": shortlist.expires_at,
        },
        "candidates": candidate_rows,
        "notice": "Missing evidence is not a low score. Every include or exclude decision is made by a person and requires a reason.",
    }


@router.put("/shortlists/{shortlist_id}/decision")
async def decide_shortlist(
    shortlist_id: int,
    body: ShortlistDecision,
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> dict[str, Any]:
    role, colleges = await _context(current_user, session)
    if role != "placements":
        raise fastapi.HTTPException(
            status_code=403, detail="Placement team permission required"
        )
    shortlist = await session.get(PracticeShortlist, shortlist_id)
    if shortlist is None:
        raise fastapi.HTTPException(status_code=404, detail="Shortlist not found")
    if shortlist.status != "draft":
        raise fastapi.HTTPException(
            status_code=409, detail="Locked shortlists are immutable"
        )
    await _authorize_student(
        session=session,
        role=role,
        allowed_colleges=colleges,
        student_id=body.student_id,
    )
    snapshot = await _evidence_snapshot(session, body.student_id, include_score=False)
    stmt = sqlalchemy.select(PracticeShortlistEntry).where(
        PracticeShortlistEntry.shortlist_id == shortlist_id,
        PracticeShortlistEntry.student_id == body.student_id,
    )
    entry = (await session.execute(stmt)).scalar_one_or_none()
    if entry is None:
        entry = PracticeShortlistEntry(
            shortlist_id=shortlist_id, student_id=body.student_id
        )
        session.add(entry)
    entry.decision = body.decision
    entry.reason = body.reason.strip()
    entry.evidence_snapshot = snapshot
    entry.decided_by = current_user.id
    await _audit(
        session,
        actor_id=current_user.id,
        action="shortlist_decision_recorded",
        purpose="opportunity_readiness",
        target_type="shortlist",
        target_id=shortlist_id,
        reason=body.reason,
        metadata={"student_id": body.student_id, "decision": body.decision},
    )
    await session.commit()
    return {"student_id": body.student_id, "decision": body.decision}


@router.post("/shortlists/{shortlist_id}/lock")
async def lock_shortlist(
    shortlist_id: int,
    body: ReasonBody,
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> dict[str, Any]:
    role, _ = await _context(current_user, session)
    if role != "placements":
        raise fastapi.HTTPException(
            status_code=403, detail="Placement team permission required"
        )
    shortlist = await session.get(PracticeShortlist, shortlist_id, with_for_update=True)
    if shortlist is None:
        raise fastapi.HTTPException(status_code=404, detail="Shortlist not found")
    if shortlist.status != "draft":
        return {
            "id": shortlist.id,
            "status": shortlist.status,
            "version": shortlist.version,
        }
    shortlist.status = "locked"
    shortlist.locked_by = current_user.id
    shortlist.locked_at = _utcnow()
    await _audit(
        session,
        actor_id=current_user.id,
        action="shortlist_locked",
        purpose="opportunity_readiness",
        target_type="shortlist",
        target_id=shortlist.id,
        reason=body.reason,
    )
    await session.commit()
    return {
        "id": shortlist.id,
        "status": shortlist.status,
        "version": shortlist.version,
    }


@router.get("/operations")
async def operations(
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> dict[str, Any]:
    role, _ = await _context(current_user, session)
    if role != "admin":
        raise fastapi.HTTPException(
            status_code=403, detail="Administrator operations permission required"
        )
    cutoff = _utcnow() - datetime.timedelta(minutes=15)
    interviews = list(
        (
            await session.execute(
                sqlalchemy.select(Interview)
                .where(Interview.interview_mode == "practice")
                .order_by(Interview.created_at.desc())
                .limit(250)
            )
        )
        .scalars()
        .all()
    )
    ids = [row.id for row in interviews]
    report_ids = set(
        (
            await session.execute(
                sqlalchemy.select(SummaryReport.interview_id).where(
                    SummaryReport.interview_id.in_(ids or [-1])
                )
            )
        )
        .scalars()
        .all()
    )
    failed_attempts = int(
        (
            await session.execute(
                sqlalchemy.select(sqlalchemy.func.count(QuestionAttempt.id)).where(
                    QuestionAttempt.interview_id.in_(ids or [-1]),
                    QuestionAttempt.transcription_status == "failed",
                )
            )
        ).scalar()
        or 0
    )
    items = []
    for row in interviews:
        issue = None
        if row.status in {"failed", "terminated"} or row.proctoring_terminated:
            issue = "interview_blocked"
        elif (
            row.status == "completed"
            and row.id not in report_ids
            and (row.completed_at or row.created_at) < cutoff
        ):
            issue = "report_delayed"
        elif row.status in {
            "active",
            "paused",
        } and row.created_at < _utcnow() - datetime.timedelta(hours=2):
            issue = "session_stuck"
        if issue:
            items.append(
                {
                    "interview_id": row.id,
                    "student_id": row.user_id,
                    "issue": issue,
                    "status": row.status,
                    "created_at": row.created_at,
                    "recoverable": not row.proctoring_terminated,
                }
            )
    return {
        "items": items,
        "failed_answer_processing": failed_attempts,
        "direct_database_editing": False,
    }


@router.get("/ai-quality")
async def ai_quality(
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> dict[str, Any]:
    role, _ = await _context(current_user, session)
    if role not in {"admin", "super_admin"}:
        raise fastapi.HTTPException(
            status_code=403, detail="AI quality permission required"
        )
    rows = list(
        (
            await session.execute(
                sqlalchemy.select(AIJob).order_by(AIJob.created_at.desc()).limit(500)
            )
        )
        .scalars()
        .all()
    )
    by_provider: dict[str, dict[str, int]] = {}
    for row in rows:
        stats = by_provider.setdefault(
            row.provider, {"total": 0, "completed": 0, "failed": 0}
        )
        stats["total"] += 1
        if row.status in stats:
            stats[row.status] += 1
    return {
        "jobs": len(rows),
        "providers": by_provider,
        "recent_failures": [
            {
                "job_type": row.job_type,
                "provider": row.provider,
                "model": row.model,
                "prompt_version": row.prompt_version,
                "error_code": row.error_code,
                "created_at": row.created_at,
            }
            for row in rows
            if row.status == "failed"
        ][:25],
        "contains_prompt_or_student_content": False,
    }


@router.get("/controls")
async def controls(
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> list[dict[str, Any]]:
    role, _ = await _context(current_user, session)
    if role not in {"admin", "super_admin"}:
        raise fastapi.HTTPException(
            status_code=403, detail="Feature control permission required"
        )
    rows = {
        row.key: row
        for row in (await session.execute(sqlalchemy.select(PracticeFeatureControl)))
        .scalars()
        .all()
    }
    return [
        {
            "key": key,
            "enabled": rows[key].enabled if key in rows else True,
            "reason": rows[key].reason if key in rows else "Default enabled",
            "updated_at": rows[key].updated_at if key in rows else None,
        }
        for key in sorted(_CONTROL_KEYS)
    ]


@router.put("/controls/{key}")
async def update_control(
    key: str,
    body: ControlUpdate,
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> dict[str, Any]:
    role, _ = await _context(current_user, session)
    if key not in _CONTROL_KEYS:
        raise fastapi.HTTPException(status_code=404, detail="Unknown feature control")
    if role not in {"admin", "super_admin"} or (
        key != "practice_new_starts" and role != "super_admin"
    ):
        raise fastapi.HTTPException(
            status_code=403,
            detail="Super Administrator approval is required for AI provider controls",
        )
    row = await session.get(PracticeFeatureControl, key, with_for_update=True)
    if row is None:
        row = PracticeFeatureControl(key=key)
        session.add(row)
    row.enabled = body.enabled
    row.reason = body.reason.strip()
    row.updated_by = current_user.id
    await _audit(
        session,
        actor_id=current_user.id,
        action="feature_control_changed",
        purpose="operations",
        target_type="feature_control",
        target_id=key,
        reason=body.reason,
        metadata={"enabled": body.enabled},
    )
    await session.commit()
    return {"key": key, "enabled": body.enabled}


@router.get("/audit")
async def audit_events(
    current_user: User = fastapi.Depends(get_current_user),
    session: AsyncSession = fastapi.Depends(get_async_session),
) -> list[dict[str, Any]]:
    role, _ = await _context(current_user, session)
    if role != "super_admin":
        raise fastapi.HTTPException(
            status_code=403, detail="Super Administrator audit permission required"
        )
    rows = list(
        (
            await session.execute(
                sqlalchemy.select(PracticeDashboardAuditEvent)
                .order_by(PracticeDashboardAuditEvent.created_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": row.id,
            "actor_id": row.actor_id,
            "action": row.action,
            "purpose": row.purpose,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "reason": row.reason,
            "metadata": row.event_metadata,
            "created_at": row.created_at,
        }
        for row in rows
    ]
