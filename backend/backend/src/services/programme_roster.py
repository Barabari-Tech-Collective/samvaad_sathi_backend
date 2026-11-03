"""Programme-scoped learner roster service.

Implements the business logic for ADR-2026-11-03:
- Pre-enrolment learner roster imports (CSV/XLSX).
- Email normalization and deduplication.
- Identity reconciliation with existing student users.
- Invite status tracking ('invited', 'invitation_resent', 'already_ready', 'manual_review').
"""

import datetime as dt
import logging
import re
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.programme_roster import ProgrammeRosterMember
from src.models.db.user import User

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def import_programme_learners(
    session: AsyncSession,
    user: Any,
    rows: list[dict[str, Any]],
    programme_scope: str = "default",
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Import and process learner rows into the programme roster.

    Returns the aggregate summary and per-row outcomes matching the frontend contract:
    {
        "accepted": int,
        "invited": int,
        "invitationResent": int,
        "alreadyReady": int,
        "manualReview": int,
        "results": [{"row": int, "status": str}]
    }
    """
    if not batch_id:
        batch_id = str(uuid.uuid4())[:8]

    now = dt.datetime.now(dt.timezone.utc)
    results: list[dict[str, Any]] = []
    seen_in_batch: set[str] = set()

    invited_count = 0
    resent_count = 0
    ready_count = 0
    manual_review_count = 0

    user_id = getattr(user, "id", None) if user else None

    for idx, row in enumerate(rows):
        source_row = row.get("sourceRow") or row.get("row") or (idx + 2)
        raw_name = str(row.get("name") or "").strip()
        raw_email = str(row.get("email") or "").strip()

        # Validate name and email
        if not raw_name or len(raw_name) > 160 or not raw_email or len(raw_email) > 254:
            results.append({"row": source_row, "status": "manual_review"})
            manual_review_count += 1
            continue

        clean_email = normalize_email(raw_email)
        if not EMAIL_REGEX.match(clean_email):
            results.append({"row": source_row, "status": "manual_review"})
            manual_review_count += 1
            continue

        if clean_email in seen_in_batch:
            # Duplicate inside same import chunk
            results.append({"row": source_row, "status": "manual_review"})
            manual_review_count += 1
            continue
        seen_in_batch.add(clean_email)

        try:
            # 1. Check if user already exists as an active student in Samvaad
            existing_user_stmt = sa.select(User).where(sa.func.lower(User.email) == clean_email)
            existing_user = (await session.execute(existing_user_stmt)).scalars().first()

            # 2. Check if a roster member already exists for this scope and email
            roster_stmt = sa.select(ProgrammeRosterMember).where(
                ProgrammeRosterMember.programme_scope == programme_scope,
                ProgrammeRosterMember.email == clean_email,
            )
            roster_member = (await session.execute(roster_stmt)).scalars().first()

            if existing_user is not None:
                # Learner is already active in Samvaad
                if roster_member is None:
                    roster_member = ProgrammeRosterMember(
                        programme_scope=programme_scope,
                        email=clean_email,
                        name=raw_name,
                        import_batch_id=batch_id,
                        invite_status="already_ready",
                        claimed_user_id=existing_user.id,
                        claimed_at=now,
                        created_by=user_id,
                        invited_at=now,
                    )
                    session.add(roster_member)
                else:
                    roster_member.claimed_user_id = existing_user.id
                    roster_member.invite_status = "already_ready"
                    if not roster_member.claimed_at:
                        roster_member.claimed_at = now
                    roster_member.name = raw_name

                results.append({"row": source_row, "status": "already_ready"})
                ready_count += 1

            elif roster_member is not None:
                # Roster member already imported before; mark invitation resent
                roster_member.invited_at = now
                roster_member.invite_status = "invitation_resent"
                roster_member.name = raw_name
                roster_member.import_batch_id = batch_id
                results.append({"row": source_row, "status": "invitation_resent"})
                resent_count += 1

            else:
                # Brand new learner invitation
                new_member = ProgrammeRosterMember(
                    programme_scope=programme_scope,
                    email=clean_email,
                    name=raw_name,
                    import_batch_id=batch_id,
                    invite_status="invited",
                    created_by=user_id,
                    invited_at=now,
                )
                session.add(new_member)
                results.append({"row": source_row, "status": "invited"})
                invited_count += 1

        except Exception as e:
            logger.exception("Failed to process roster row %d (%s): %s", source_row, clean_email, e)
            results.append({"row": source_row, "status": "manual_review"})
            manual_review_count += 1

    await session.commit()

    accepted_count = invited_count + resent_count + ready_count

    return {
        "accepted": accepted_count,
        "invited": invited_count,
        "invitationResent": resent_count,
        "alreadyReady": ready_count,
        "manualReview": manual_review_count,
        "results": results,
    }
