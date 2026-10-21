import logging
import httpx
from src.config.manager import settings

logger = logging.getLogger(__name__)


async def provision_or_invite_staff(
    email: str,
    name: str,
    role: str = "STAFF",
    send_invite_email: bool = True,
) -> str | None:
    """
    Calls Central Auth (barabari-auth-service) to provision or link a staff user identity.
    Returns the centralUserId (UUID) if successful, or None if Central Auth is not reachable or unconfigured.
    """
    if not settings.AUTH_SERVICE_BASE_URL or not settings.CENTRAL_PROFILE_API_KEY:
        logger.warning("[central-auth] Central Auth bridge is not configured; skipping provisioning call.")
        return None

    url = f"{settings.AUTH_SERVICE_BASE_URL.rstrip('/')}/barabari-auth/api/auth/internal/v1/users/provision-or-invite"
    headers = {
        "X-Internal-Api-Key": settings.CENTRAL_PROFILE_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "email": email.strip().lower(),
        "name": name.strip(),
        "productUniqueId": settings.SAMPARK_PRODUCT_UNIQUE_ID,
        "role": role.strip().upper() if role else "STAFF",
        "sendInviteEmail": send_invite_email,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if resp.status_code in (200, 201) and body.get("isSuccess"):
                data = body.get("data") or {}
                central_user_id = data.get("centralUserId")
                logger.info(
                    f"[central-auth] Successfully provisioned/linked user {email} (isNewUser={data.get('isNewUser')}) -> centralUserId={central_user_id}"
                )
                return str(central_user_id) if central_user_id else None
            else:
                err_msg = body.get("errorMessage") or f"Status {resp.status_code}"
                logger.error(f"[central-auth] Provisioning failed for {email}: {err_msg}")
                return None
    except Exception as exc:
        logger.error(f"[central-auth] HTTP error during provisioning for {email}: {exc}")
        return None
