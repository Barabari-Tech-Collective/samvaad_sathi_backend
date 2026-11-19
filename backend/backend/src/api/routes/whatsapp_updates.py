"""The signed-in student's "WhatsApp updates" setting (see services/whatsapp_updates.py)."""

import fastapi
from pydantic import BaseModel

from src.api.dependencies.auth import get_current_user
from src.services import whatsapp_updates as service

router = fastapi.APIRouter(prefix="/v2/whatsapp-updates", tags=["whatsapp-updates"])


class Setting(BaseModel):
    on: bool


@router.get("/me")
async def mine(response: fastapi.Response, user=fastapi.Depends(get_current_user)):
    """Whether this student gets WhatsApp updates (on unless they turned them off)."""
    response.headers["Cache-Control"] = "no-store"
    return await service.get(user)


@router.put("/me")
async def change(body: Setting, response: fastapi.Response, user=fastapi.Depends(get_current_user)):
    """Turn WhatsApp updates on or off. Off is the same as replying STOP on WhatsApp."""
    response.headers["Cache-Control"] = "no-store"
    return await service.put(user, body.on)
