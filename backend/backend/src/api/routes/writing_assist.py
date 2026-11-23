"""Rephrase with AI, for any staff text box. Returns a suggestion; nothing is stored."""

from typing import Literal

import fastapi
from pydantic import BaseModel, Field, field_validator

from src.api.dependencies.admin import get_current_admin_user
from src.services import writing_assist

router = fastapi.APIRouter(prefix="/v2/writing", tags=["writing-assist"])


class Ask(BaseModel):
    text: str = Field(min_length=3, max_length=4000)
    purpose: Literal[
        "learner_feedback",
        "interview_note",
        "private_note",
        "handoff_note",
        "answer_remark",
        "instruction",
        "reason",
        "description",
        "question",
        "message",
        "general",
    ] = "general"

    @field_validator("text", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


@router.post("/rephrase")
async def rephrase(
    body: Ask,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
):
    response.headers["Cache-Control"] = "no-store"
    text = await writing_assist.rephrase(body.text, body.purpose)
    if text is None:
        raise fastapi.HTTPException(
            503, "AI rewriting is not available right now. Your text is unchanged."
        )
    return {"text": text[:4000]}
