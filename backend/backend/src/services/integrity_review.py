"""Auditable human decisions and independent appeals, never automated penalties."""

import datetime as dt
import hashlib
import json
import fastapi
import sqlalchemy as sa
from src.api.dependencies.admin import is_admin_user
from src.models.db.integrity_case import IntegrityCase
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.services.course_access import grants_for, require_course
from src.services.feature_access import permits

UTC = dt.timezone.utc
POLICY = "human-integrity-review-v1"
NOTICE = "A flag is not proof of cheating. A review does not change a score by itself. You can explain what happened within 14 days."


def permitted(user, operation):
    return is_admin_user(user) or (
        isinstance(user.feature_permissions, dict)
        and permits(user.feature_permissions, "integrity_review", operation)
    )


async def scope(user, course_id, session, operation):
    if not permitted(user, operation):
        raise fastapi.HTTPException(
            403,
            "This integrity review operation requires an explicit administrator grant.",
        )
    grants = await grants_for(user, session)
    require_course(course_id, grants, user)
    if is_admin_user(user):
        return sa.true()
    clauses = []
    for grant in grants:
        if course_id not in grant.course_ids:
            continue
        if grant.colleges is None:
            return sa.true()
        clauses.append(
            sa.func.lower(sa.func.trim(IntegrityCase.college)).in_(grant.colleges)
        )
    return sa.or_(*clauses) if clauses else sa.false()


def fingerprint(body):
    return hashlib.sha256(
        json.dumps(
            body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def audit(session, user, row, action):
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id,
            action=f"integrity_{action}",
            purpose="assessment_integrity",
            target_type="integrity_case",
            target_id=str(row.id),
            reason="Assessment integrity workflow",
            event_metadata={"course_id": row.course_id, "version": row.version},
        )
    )


def capabilities(row, user, now, *, staff=False):
    return {
        "can_redact": staff and is_admin_user(user),
        "can_explain": not staff
        and row.state in {"open", "appealed"}
        and sum(e["kind"] == "explain" for e in row.history) < 3,
        "can_appeal": not staff
        and row.state == "decided"
        and row.appeal_deadline is not None
        and now < row.appeal_deadline,
        "can_decide": staff
        and row.state == "open"
        and user.id != row.student_id
        and permitted(user, "publish"),
        "can_resolve_appeal": staff
        and row.state == "appealed"
        and user.id not in {row.decided_by, row.student_id, row.opened_by}
        and permitted(user, "resolve_appeal"),
        "can_add_evidence": staff and row.state == "open" and permitted(user, "update"),
        "can_reopen": staff
        and is_admin_user(user)
        and row.state in {"decided", "resolved"}
        and sum(e["kind"] == "decide" for e in row.history) < 3,
    }


def serialize(row, user, now, *, staff=False):
    # Only explicitly shared, written evidence is stored here. No raw recording,
    # transcript, hidden notes, internal request hashes or staff IDs reach learners.
    redactions = {
        e["target_index"]: e["reason"] for e in row.history if e["kind"] == "redact"
    }
    history = []
    for index, event in enumerate(row.history):
        public = {
            key: event[key]
            for key in ("kind", "reason", "source", "outcome", "at", "signals")
            if key in event
        }
        public["event_index"] = index
        if event["kind"] == "redact":
            public["reason"] = "An earlier entry was redacted by an administrator."
        elif index in redactions:
            public["reason"] = redactions[index]
            public.pop("signals", None)
            public["redacted"] = True
        history.append(public)

    return {
        "id": row.id,
        "interview_id": row.interview_id,
        "course_id": row.course_id,
        "state": row.state,
        "version": row.version,
        "outcome": row.outcome,
        "appeal_deadline": row.appeal_deadline,
        "history": history,
        "notice": NOTICE,
        "policy_version": POLICY,
        **capabilities(row, user, now, staff=staff),
    }


def transition(row, user, body, now, *, staff):
    digest, key = fingerprint(body), str(body.request_key)
    prior = next((e for e in row.history if e.get("request_key") == key), None)
    if prior:
        if prior.get("request_hash") != digest or prior["actor_id"] != user.id:
            raise fastapi.HTTPException(
                409, "This request key already records a different action."
            )
        return False
    if row.version != body.expected_version:
        raise fastapi.HTTPException(
            409,
            "The case changed. Reload and review the current evidence before submitting.",
        )
    if len(row.history) >= 150 or (
        body.action == "add_evidence" and len(row.history) >= 30
    ):
        raise fastapi.HTTPException(
            409, "Case history limit reached. Contact an administrator."
        )
    caps = capabilities(row, user, now, staff=staff)
    action = body.action
    if not caps.get(f"can_{action}", False):
        raise fastapi.HTTPException(
            409,
            "This action is unavailable, expired or requires an independent reviewer.",
        )
    if action == "redact":
        if (
            body.target_index >= len(row.history)
            or row.history[body.target_index]["kind"] == "redact"
        ):
            raise fastapi.HTTPException(
                422, "Choose an existing evidence/decision entry to redact."
            )
        if row.state == "decided":
            row.appeal_deadline = now + dt.timedelta(days=14)
    elif action in {"decide", "resolve_appeal"}:
        if body.outcome == "concern" and not any(
            e.get("source") in {"interviewer_observation", "verified_artifact"}
            for e in row.history
        ):
            raise fastapi.HTTPException(
                422,
                "A browser signal alone cannot support a concern finding. Add corroborating human evidence or choose inconclusive/clear.",
            )
        row.outcome = body.outcome
        if action == "decide":
            row.state, row.decided_by = "decided", user.id
            row.appeal_deadline = now + dt.timedelta(days=14)
        else:
            row.state = "resolved"
    elif action == "appeal":
        row.state = "appealed"
    elif action == "reopen":
        row.state, row.outcome, row.appeal_deadline = "open", None, None
    event = {
        "kind": action,
        "reason": body.reason,
        "actor_id": user.id,
        "at": now.isoformat(),
        "request_key": key,
        "request_hash": digest,
    }
    if staff and action == "redact":
        event["target_index"] = body.target_index
    if staff and body.source:
        event["source"] = body.source
    if staff and body.outcome:
        event["outcome"] = body.outcome
    row.history = [*row.history, event]
    row.version += 1
    return True
