"""Opportunity access and evidence (architecture pack F25).

Access is the shortlist rule, unchanged: the placement-shortlist permission for the
operation, plus a placement responsibility on the opportunity's course (admins pass).

Candidates are the course's students within that responsibility. Unlike a practice
shortlist, no learning-sharing consent is required: the pack is explicit that declining
private learning sharing is not a rejection signal. Evidence for an opportunity therefore
comes only from formal assessments and human rounds; whether a student shares mock data is
reported as a yes/no, never the data itself.
"""

import datetime

import fastapi
import sqlalchemy as sa

from src.api.dependencies.admin import is_admin_user
from src.models.db.human_interview import HumanInterview
from src.models.db.interview import Interview
from src.models.db.practice_governance import PracticeConsent
from src.models.db.user import User
from src.services import course_shortlists as shortlists
from src.services.course_access import student_predicate
from src.services.people import student_only

UTC = datetime.timezone.utc


async def authorize(user, course_id, session, operation):
    """Refuse unless this person may do `operation` on this course's opportunities.
    Returns the predicate selecting students who may be candidates."""
    if not shortlists.permission(user, operation):
        raise fastapi.HTTPException(
            403, "An administrator must enable opportunities for your account."
        )
    grants = await shortlists.placement_grants(user, course_id, session)
    if not is_admin_user(user) and not grants:
        raise fastapi.HTTPException(
            403, "A placement responsibility assigned to this course is required."
        )
    return sa.and_(
        student_predicate(course_id, grants, global_access=is_admin_user(user)),
        student_only(),
    )


def shares_mock_data(now):
    """WHERE clause on User: the student currently shares learning data for opportunities."""
    return sa.exists(
        sa.select(PracticeConsent.id).where(
            PracticeConsent.user_id == User.id,
            PracticeConsent.purpose == "opportunity_readiness",
            PracticeConsent.granted.is_(True),
            PracticeConsent.withdrawn_at.is_(None),
            sa.or_(PracticeConsent.expires_at.is_(None), PracticeConsent.expires_at > now),
        )
    )


async def evidence(session, opportunity, student_ids, now):
    """Formal evidence per candidate: finished assessments in this course within the
    opportunity's age limit, and this opportunity's human rounds."""
    if not student_ids:
        return {}
    since = now - datetime.timedelta(days=opportunity.max_evidence_age_days)
    finished = [
        Interview.user_id.in_(student_ids),
        Interview.course_id == opportunity.course_id,
        Interview.interview_mode == "assessment",
        Interview.status == "completed",
        Interview.completed_at >= since,
    ]
    counts = {
        uid: (int(n), latest)
        for uid, n, latest in (
            await session.execute(
                sa.select(
                    Interview.user_id,
                    sa.func.count(Interview.id),
                    sa.func.max(Interview.completed_at),
                )
                .where(*finished)
                .group_by(Interview.user_id)
            )
        ).all()
    }
    for_role = {}
    if opportunity.job_profile_id is not None:
        for_role = {
            uid: int(n)
            for uid, n in (
                await session.execute(
                    sa.select(Interview.user_id, sa.func.count(Interview.id))
                    .where(*finished, Interview.job_profile_id == opportunity.job_profile_id)
                    .group_by(Interview.user_id)
                )
            ).all()
        }
    rounds = {}
    for row in (
        await session.execute(
            sa.select(HumanInterview)
            .where(
                HumanInterview.opportunity_id == opportunity.id,
                HumanInterview.student_id.in_(student_ids),
            )
            .order_by(HumanInterview.starts_at)
        )
    ).scalars():
        rounds.setdefault(row.student_id, []).append(
            {
                "id": row.id,
                "round": row.role_name or row.title,
                "starts_at": row.starts_at.isoformat(),
                "status": row.status,
                "result": row.result,
                "result_released": row.result_released_at is not None,
            }
        )
    out = {}
    for uid in student_ids:
        n, latest = counts.get(uid, (0, None))
        out[uid] = {
            "assessments": n,
            "assessments_for_role": for_role.get(uid) if opportunity.job_profile_id else None,
            "latest_assessment_at": latest.isoformat() if latest else None,
            "human_rounds": rounds.get(uid, []),
            # "missing" (none at all) and "needs_more" (some, below the rule) are different
            # states; neither is a rejection.
            "state": (
                "ready"
                if n >= opportunity.min_assessments
                else "needs_more" if n else "missing"
            ),
        }
    return out
