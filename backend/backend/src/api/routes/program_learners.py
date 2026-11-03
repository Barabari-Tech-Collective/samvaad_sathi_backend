"""Programme-scoped learner roster routes (ADR-2026-11-03)."""

from typing import Any

import fastapi
from pydantic import BaseModel, Field

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.session import get_async_session
from src.services import programme_roster as roster_service

router = fastapi.APIRouter(prefix="/v2/program-learners", tags=["program-learners"])


class LearnerRowInput(BaseModel):
    name: str = Field(..., max_length=160)
    email: str = Field(..., max_length=254)
    sourceRow: int | None = None
    row: int | None = None


class ImportRosterPayload(BaseModel):
    data: dict[str, Any] | None = None
    rows: list[LearnerRowInput] | None = None
    programme_scope: str | None = None


@router.post("/import", response_model=None)
async def import_learners(
    request: fastapi.Request,
    response: fastapi.Response,
    payload: ImportRosterPayload | None = None,
    scope: str = fastapi.Query("default"),
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Import and invite programme learners in batch from CSV or Excel uploads."""
    response.headers["Cache-Control"] = "no-store"

    extracted_rows: list[dict[str, Any]] = []
    effective_scope = (payload and payload.programme_scope) or scope or "default"

    if payload and payload.rows:
        extracted_rows = [r.model_dump() for r in payload.rows]
    elif payload and payload.data and isinstance(payload.data.get("rows"), list):
        extracted_rows = [
            r if isinstance(r, dict) else dict(r) for r in payload.data["rows"]
        ]
        if "programme_scope" in payload.data and isinstance(payload.data["programme_scope"], str):
            effective_scope = payload.data["programme_scope"]
    else:
        try:
            body = await request.json()
            if isinstance(body, dict):
                if "rows" in body and isinstance(body["rows"], list):
                    extracted_rows = body["rows"]
                elif "data" in body and isinstance(body["data"], dict) and "rows" in body["data"]:
                    extracted_rows = body["data"]["rows"]
                    if "programme_scope" in body["data"] and isinstance(body["data"]["programme_scope"], str):
                        effective_scope = body["data"]["programme_scope"]
                if "programme_scope" in body and isinstance(body["programme_scope"], str):
                    effective_scope = body["programme_scope"]
        except Exception:
            pass

    summary = await roster_service.import_programme_learners(
        session=session,
        user=user,
        rows=extracted_rows,
        programme_scope=effective_scope,
    )

    return {
        "isSuccess": True,
        "isError": False,
        "errorMessage": None,
        "data": summary,
    }
