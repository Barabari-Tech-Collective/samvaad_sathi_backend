"""Versioned formal selection. No private-learning consent or evidence enters this flow."""

import datetime as dt
import fastapi
import sqlalchemy as sa
from src.models.db.interview import Interview
from src.models.db.human_interview import HumanInterview
from src.models.db.opportunity import OpportunityCandidate
from src.models.db.opportunity_selection import (
    OpportunitySelection,
    OpportunitySelectionEntry,
)
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.user import User
from src.services import course_shortlists

UTC = dt.timezone.utc


def rules(opening):
    return {
        "eligibility_version": opening.eligibility_version,
        "max_evidence_age_days": opening.max_evidence_age_days,
        "min_assessments": opening.min_assessments,
        "job_profile_id": opening.job_profile_id,
        "course_id": opening.course_id,
    }


def metadata(row, opening):
    return {
        "opportunity_id": opening.id,
        "selection_id": row.id if row else None,
        "revision": row.revision if row else 1,
        "version": row.version if row else 0,
        "status": row.status if row else "draft",
        "rules": row.rules if row else rules(opening),
        "locked_at": row.locked_at if row else None,
        "released_at": row.released_at if row else None,
    }


async def latest(session, opportunity_id):
    return (
        await session.execute(
            sa.select(OpportunitySelection)
            .where(OpportunitySelection.opportunity_id == opportunity_id)
            .order_by(OpportunitySelection.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def evidence(session, opening, student_ids, now=None):
    now = now or dt.datetime.now(UTC)
    since = now - dt.timedelta(days=opening.max_evidence_age_days)
    result = {
        sid: {
            "assessment_ids": [],
            "human_round_ids": [],
            "assessments": [],
            "human_rounds": [],
            "latest_at": None,
        }
        for sid in student_ids
    }
    if not student_ids:
        return result
    ai = (
        await session.execute(
            sa.select(Interview).where(
                Interview.user_id.in_(student_ids),
                Interview.course_id == opening.course_id,
                Interview.interview_mode == "assessment",
                Interview.status == "completed",
                Interview.completed_at.between(since, now),
            )
        )
    ).scalars()
    for row in ai:
        e = result[row.user_id]
        e["assessment_ids"].append(row.id)
        when = row.completed_at.isoformat()
        e["assessments"].append(
            {
                "id": row.id,
                "state_version": row.state_version,
                "question_set_version": row.question_set_version,
                "completed_at": when,
            }
        )
        e["latest_at"] = max(e["latest_at"] or when, when)
    human = (
        await session.execute(
            sa.select(HumanInterview).where(
                HumanInterview.student_id.in_(student_ids),
                HumanInterview.course_id == opening.course_id,
                HumanInterview.opportunity_id == opening.id,
                HumanInterview.status == "completed",
                HumanInterview.closed_at.between(since, now),
                HumanInterview.result.is_not(None),
            )
        )
    ).scalars()
    for row in human:
        e = result[row.student_id]
        e["human_round_ids"].append(row.id)
        when = row.closed_at.isoformat()
        e["human_rounds"].append(
            {
                "id": row.id,
                "result": row.result,
                "feedback": row.feedback,
                "closed_at": when,
            }
        )
        e["latest_at"] = max(e["latest_at"] or when, when)
    for e in result.values():
        e["assessments"].sort(key=lambda item: item["id"])
        e["human_rounds"].sort(key=lambda item: item["id"])
        e["assessment_ids"].sort()
        e["human_round_ids"].sort()
        e["count"] = len(e["assessment_ids"]) + len(e["human_round_ids"])
        e["state"] = (
            "ready"
            if e["count"] >= opening.min_assessments
            else "needs_more" if e["count"] else "missing"
        )
    return result


async def candidates(session, opening, predicate):
    return (
        (
            await session.execute(
                sa.select(User)
                .join(OpportunityCandidate, OpportunityCandidate.student_id == User.id)
                .where(OpportunityCandidate.opportunity_id == opening.id, predicate)
                .order_by(User.name, User.id)
            )
        )
        .scalars()
        .all()
    )


def audit(session, user, opening, row, action, reason, details=None):
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id,
            action=action,
            purpose="opportunity_readiness",
            target_type="opportunity_selection",
            target_id=str(row.id),
            reason=reason,
            event_metadata={
                "opportunity_id": opening.id,
                "course_id": opening.course_id,
                "revision": row.revision,
                "version": row.version,
                **(details or {}),
            },
        )
    )


def verify_version(row, expected):
    if expected != (row.version if row else 0):
        raise fastapi.HTTPException(409, "This review changed. Refresh before saving.")


def require_draft(row, opening):
    if opening.status != "open" or (row and row.status != "draft"):
        raise fastapi.HTTPException(
            409,
            "Open the opportunity and use a draft review revision before changing decisions.",
        )
    if row and row.rules != rules(opening):
        raise fastapi.HTTPException(
            409, "Eligibility rules changed. Start a new review revision."
        )


def describe(entry):
    if not entry:
        return {"recommendation": None, "decision": None}
    return {
        "recommendation": (
            {
                "decision": entry.recommendation,
                "reason": entry.recommendation_reason,
                "by": entry.recommended_by,
                "at": entry.recommended_at.isoformat(),
            }
            if entry.recommendation
            else None
        ),
        "decision": (
            {
                "decision": entry.decision,
                "reason": entry.decision_reason,
                "student_feedback": entry.student_feedback,
                "next_step": entry.next_step,
                "by": entry.decided_by,
                "at": entry.decided_at.isoformat(),
            }
            if entry.decision
            else None
        ),
    }


async def detail(session, user, opening, row, predicate):
    people = await candidates(session, opening, predicate)
    entries = (
        {
            e.student_id: e
            for e in (
                await session.execute(
                    sa.select(OpportunitySelectionEntry).where(
                        OpportunitySelectionEntry.selection_id == row.id
                    )
                )
            ).scalars()
        }
        if row
        else {}
    )
    ev = await evidence(session, opening, [p.id for p in people])
    items = [
        {
            "student_id": p.id,
            "name": p.name,
            "college": p.university,
            "evidence": ev[p.id],
            **describe(entries.get(p.id)),
        }
        for p in people
    ]
    # A locked version always displays its frozen evidence/decisions, filtered to current scope.
    if row and row.snapshot:
        frozen_ids = [item["student_id"] for item in row.snapshot["items"]]
        allowed = set(
            (
                await session.execute(
                    sa.select(User.id).where(User.id.in_(frozen_ids), predicate)
                )
            ).scalars()
        )
        items = [
            item for item in row.snapshot["items"] if item["student_id"] in allowed
        ]
    total = (
        await session.execute(
            sa.select(sa.func.count())
            .select_from(OpportunityCandidate)
            .where(OpportunityCandidate.opportunity_id == opening.id)
        )
    ).scalar_one()
    blockers = []
    if not total:
        blockers.append("Add candidates before locking.")
    if total != len(people):
        blockers.append(
            "Some candidates are no longer in your current scope. Resolve membership or use an authorized reviewer."
        )
    if row and row.rules != rules(opening):
        blockers.append("Eligibility rules changed. Start a new review revision.")
    for p in people:
        entry = entries.get(p.id)
        if not entry or not entry.decision:
            blockers.append(f"{p.name}: a lead decision is pending.")
        elif ev[p.id]["state"] != "ready":
            blockers.append(f"{p.name}: fresh formal evidence is required.")
        elif entry.evidence_snapshot != ev[p.id]:
            blockers.append(
                f"{p.name}: evidence changed since the decision. Review and save it again."
            )
    publish = course_shortlists.permission(user, "publish")
    draft = opening.status == "open" and (not row or row.status == "draft")
    return {
        **metadata(row, opening),
        "items": items,
        "lock_blockers": blockers,
        "can_recommend": draft and course_shortlists.permission(user, "update"),
        "can_decide": draft and publish,
        "can_lock": draft and publish,
        "can_release": bool(
            row and row.status == "locked" and publish and opening.status != "archived"
        ),
        "can_revise": publish and opening.status == "open" and row is not None,
        "history": [
            {
                "revision": r.revision,
                "status": r.status,
                "locked_at": r.locked_at,
                "released_at": r.released_at,
            }
            for r in (
                await session.execute(
                    sa.select(OpportunitySelection)
                    .where(OpportunitySelection.opportunity_id == opening.id)
                    .order_by(OpportunitySelection.revision.desc())
                )
            ).scalars()
        ],
    }
