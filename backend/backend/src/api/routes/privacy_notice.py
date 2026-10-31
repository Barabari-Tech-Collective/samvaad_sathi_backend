"""The privacy notice a learner accepts once, at signup (one plain page, one tick).

Not a per-feature choice: accepting does not switch any collection on or off. The wording below
is a plain draft for the programme owner and their counsel to confirm before launch."""

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.privacy_notice import PrivacyNoticeAcceptance

router = fastapi.APIRouter(prefix="/v2/privacy-notice", tags=["privacy-notice"])

VERSION = "2026-10"
NOTICE = {
    "version": VERSION,
    "title": "How we use your information",
    "paragraphs": [
        "To run the programme we keep your practice recordings, transcripts, scores, resume and "
        "interview results.",
        "Programme staff you work with, and people responsible for placements, can see this to coach "
        "you and to help with opportunities.",
        "AI services process your recordings and answers to give you feedback.",
        "Ask your programme team if you have questions about your information.",
    ],
}


class Accept(BaseModel):
    version: str


@router.get("")
async def current(
    user=fastapi.Depends(get_current_user), session=fastapi.Depends(get_async_session)
):
    accepted = (
        await session.execute(
            sa.select(PrivacyNoticeAcceptance.accepted_at).where(
                PrivacyNoticeAcceptance.user_id == user.id, PrivacyNoticeAcceptance.version == VERSION
            )
        )
    ).scalar_one_or_none()
    return NOTICE | {"accepted": accepted is not None, "accepted_at": accepted}


@router.post("/accept")
async def accept(
    body: Accept, user=fastapi.Depends(get_current_user), session=fastapi.Depends(get_async_session)
):
    if body.version != VERSION:
        raise fastapi.HTTPException(409, "The notice changed. Reload it.")
    await session.execute(
        insert(PrivacyNoticeAcceptance)
        .values(user_id=user.id, version=VERSION)
        .on_conflict_do_nothing(constraint="uq_privacy_notice_user_version")
    )
    await session.commit()
    return {"version": VERSION, "accepted": True}
