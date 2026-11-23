"""The student's own "WhatsApp updates" setting (suite phase 2D).

Sampark keeps the decision (every student is on by default; off is the same as replying STOP).
Samvaad only shows it and passes the student's change on, using the same Sampark connection as
the batch list. Students without a Sampark id have no WhatsApp updates to switch.
"""

from __future__ import annotations

import logging

import fastapi
import httpx

from src.services.barabari_integration import batches_connection
from src.services.student_placement import describe_failure

logger = logging.getLogger(__name__)
PATH = "/samvaad-saathi/v1/students/{sid}/whatsapp-updates"
UNAVAILABLE = "Your WhatsApp setting could not be loaded right now. Please try again in a minute."
NOT_SAVED = "Your WhatsApp setting was not changed. Please try again in a minute."


def _student_id(user) -> str:
    sid = getattr(user, "student_id", None)
    if not sid:
        raise fastapi.HTTPException(status_code=404, detail="WhatsApp updates are only for Barabari students.")
    return sid


async def _call(method: str, sid: str, body: dict | None, *, transport=None) -> dict:
    base, key = batches_connection()
    if not base or not key:
        raise RuntimeError("the Sampark base URL or service key is not set")
    async with httpx.AsyncClient(timeout=8.0, transport=transport) as client:
        response = await client.request(
            method, f"{base}{PATH.format(sid=sid)}", headers={"SAMVAAD-SAATHI-API-KEY": key}, json=body
        )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("on"), bool):
        raise ValueError("Unexpected response from Sampark")
    return {"on": data["on"], "decided_at": data.get("decidedAt")}


async def get(user, *, transport=None) -> dict:
    sid = _student_id(user)
    try:
        return await _call("GET", sid, None, transport=transport)
    except Exception as exc:  # the reason is logged; the student sees a plain sentence
        logger.warning("whatsapp_updates_read_failed: %s", describe_failure(exc))
        raise fastapi.HTTPException(status_code=503, detail=UNAVAILABLE) from None


async def put(user, on: bool, *, transport=None) -> dict:
    sid = _student_id(user)
    try:
        return await _call("PUT", sid, {"on": on}, transport=transport)
    except Exception as exc:
        logger.warning("whatsapp_updates_write_failed: %s", describe_failure(exc))
        raise fastapi.HTTPException(status_code=503, detail=NOT_SAVED) from None
