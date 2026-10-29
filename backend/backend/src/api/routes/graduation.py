from datetime import date, datetime, timezone
from typing import Literal
import fastapi
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from pydantic import BaseModel, Field, ConfigDict, model_validator
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.session import get_async_session
from src.services.course_access import grants_for, require_course, student_predicate
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.models.db.graduation import Graduation, GraduationEvent
from src.services import graduation as service

router = fastapi.APIRouter(prefix="/v2/graduation", tags=["graduation"])


class Change(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    expected_version: int = Field(ge=0, strict=True)
    action: Literal["verify_course", "verify_placement", "approve", "reopen"]
    reason: str = Field(min_length=10, max_length=1000)
    evidence_reference: str | None = Field(default=None, min_length=10, max_length=1000)
    employer: str | None = Field(default=None, min_length=2, max_length=160)
    job_title: str | None = Field(default=None, min_length=2, max_length=160)
    joined_on: date | None = None

    @model_validator(mode="after")
    def validate_evidence(self):
        if self.action.startswith("verify_") and not self.evidence_reference:
            raise ValueError("A reviewed evidence reference is required.")
        if self.action == "verify_placement" and (not self.employer or not self.job_title or not self.joined_on):
            raise ValueError("Employer, job title and actual joining date are required.")
        if self.joined_on and self.joined_on > date.today():
            raise ValueError("An offer or future joining date is not verified employment.")
        return self


async def scope_predicate(session, user, course_id, operation):
    if not service.allowed(user, operation):
        raise fastapi.HTTPException(403, "Ask an administrator to grant this graduation operation.")
    grants = await grants_for(user, session)
    require_course(course_id, grants, user)
    # Graduation must not require sharing private practice recordings.
    return sa.and_(student_predicate(course_id, grants, global_access=is_admin_user(user)),
        User.role.is_(None), User.is_admin.is_(False), User.is_super_admin.is_(False),
        ~sa.exists(sa.select(TeamMember.id).where(TeamMember.user_id == User.id, TeamMember.status == "active")))


async def authorize(session, user, course_id, student_id, operation):
    predicate = await scope_predicate(session, user, course_id, operation)
    student = (await session.execute(sa.select(User.id).where(User.id == student_id, predicate))).scalar_one_or_none()
    if student is None:
        raise fastapi.HTTPException(404, "Student unavailable in your current course scope.")


@router.get("/courses/{course_id}/students")
async def roster(course_id: str, response: fastapi.Response, page: int = fastapi.Query(1, ge=1), user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session)):
    response.headers['Cache-Control'] = 'no-store'
    predicate = await scope_predicate(session, user, course_id, 'read')
    total = (await session.execute(sa.select(sa.func.count(User.id)).where(predicate))).scalar_one()
    rows = (await session.execute(sa.select(User.id, User.name, Graduation.state).outerjoin(Graduation, sa.and_(Graduation.student_id == User.id, Graduation.course_id == course_id)).where(predicate).order_by(User.id).offset((page-1)*25).limit(25))).all()
    return {"total":total,"items":[{"student_id":r.id,"name":r.name,"state":r.state or "not_started"} for r in rows]}


@router.get("/me")
async def mine(response: fastapi.Response, user=fastapi.Depends(get_current_user), session=fastapi.Depends(get_async_session)):
    response.headers['Cache-Control'] = 'no-store'
    rows = (await session.execute(sa.select(Graduation).where(Graduation.student_id == user.id).order_by(Graduation.course_id))).scalars().all()
    return {"items": [service.project(row) for row in rows]}


@router.get("/courses/{course_id}/students/{student_id}")
async def detail(course_id: str, student_id: int, response: fastapi.Response, user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session)):
    response.headers['Cache-Control'] = 'no-store'
    await authorize(session, user, course_id, student_id, 'read')
    row = await session.get(Graduation, (student_id, course_id))
    events = (await session.execute(sa.select(GraduationEvent).where(GraduationEvent.student_id == student_id, GraduationEvent.course_id == course_id).order_by(GraduationEvent.version.desc()).limit(100))).scalars().all()
    return {"record": service.project(row, private=True) if row else {"student_id":student_id,"course_id":course_id,"version":0,"state":"draft","course_verified":False,"placement_verified":False,"graduated_at":None},
            "operations": [op for op in service.OPERATIONS if service.allowed(user, op)],
            "history": [{"version": e.version, "actor_id":e.actor_id,"action":e.action,"reason":e.reason,"at":e.snapshot.get("event_at"),"snapshot":e.snapshot} for e in events]}


@router.post("/courses/{course_id}/students/{student_id}")
async def change(course_id: str, student_id: int, body: Change, user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session)):
    if not course_id.strip() or len(course_id) > 128:
        raise fastapi.HTTPException(422, "Invalid course ID.")
    await authorize(session, user, course_id, student_id, body.action)
    await session.execute(insert(Graduation).values(student_id=student_id, course_id=course_id, version=0, state="draft").on_conflict_do_nothing())
    row = (await session.execute(sa.select(Graduation).where(Graduation.student_id == student_id, Graduation.course_id == course_id).with_for_update())).scalar_one()
    snapshot = service.transition(row, body, user)
    snapshot["event_at"] = datetime.now(timezone.utc).isoformat()
    session.add(GraduationEvent(student_id=student_id, course_id=course_id, version=row.version, actor_id=user.id, action=body.action, reason=body.reason, snapshot=snapshot))
    await session.commit()
    return snapshot
