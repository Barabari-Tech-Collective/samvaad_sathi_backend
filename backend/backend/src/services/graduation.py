"""Human verification only: scores and AI recommendations never graduate a learner."""
from datetime import datetime, timezone
from fastapi import HTTPException
from src.api.dependencies.admin import is_admin_user
from src.services.feature_access import permits

OPERATIONS = ("read", "verify_course", "verify_placement", "approve", "reopen")


def allowed(user, operation):
    if operation in {"approve", "reopen"} and not is_admin_user(user):
        return False
    return is_admin_user(user) or (isinstance(user.feature_permissions, dict) and permits(user.feature_permissions, "graduation", operation))


def project(row, *, private=False):
    value = {"student_id": row.student_id, "course_id": row.course_id, "version": row.version,
             "state": row.state, "course_verified": bool(row.course_evidence),
             "placement_verified": bool(row.placement_evidence),
             "graduated_at": row.approval.get("at") if row.approval else None}
    if private:
        value.update(course_evidence=row.course_evidence, placement_evidence=row.placement_evidence, approval=row.approval)
    return value


def transition(row, body, actor):
    if not allowed(actor, body.action):
        raise HTTPException(403, "This graduation operation is not available for your account.")
    if actor.id == row.student_id:
        raise HTTPException(403, "You cannot verify or approve your own graduation.")
    if body.expected_version != row.version:
        raise HTTPException(409, "This record changed. Reload before making a decision.")
    now = datetime.now(timezone.utc).isoformat()
    if body.action == "reopen":
        if row.state != "graduated":
            raise HTTPException(409, "Only a graduated record can be reopened.")
        row.course_evidence = row.placement_evidence = row.approval = None
    elif row.state == "graduated":
        raise HTTPException(409, "An administrator must reopen graduation before changing evidence.")
    elif body.action == "approve":
        if not row.course_evidence or not row.placement_evidence:
            raise HTTPException(409, "Verify course completion and job placement before approval.")
        if actor.id in {row.course_evidence['actor_id'], row.placement_evidence['actor_id']}:
            raise HTTPException(409, "A different administrator must approve independently verified evidence.")
        row.approval = {"actor_id": actor.id, "at": now}
    else:
        evidence = {"actor_id": actor.id, "at": now, "reference": body.evidence_reference}
        if body.action == "verify_course":
            row.course_evidence = evidence
        elif body.action == "verify_placement":
            evidence.update(employer=body.employer, job_title=body.job_title, joined_on=body.joined_on.isoformat())
            row.placement_evidence = evidence
        else:
            raise HTTPException(400, "Unknown graduation action.")
    row.state = "graduated" if row.approval else "ready" if row.course_evidence and row.placement_evidence else "draft"
    row.version += 1
    return project(row, private=True)
