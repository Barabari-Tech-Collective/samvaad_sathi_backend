import logging
import re

import httpx
from src.config.manager import settings

logger = logging.getLogger(__name__)


class CentralAuthProvisioningError(Exception):
    """Raised when staff provisioning or invitation in Central Auth fails, is misconfigured, or unreachable."""
    pass


class CentralAuthNonStaffAccountError(CentralAuthProvisioningError):
    """Raised when the email already belongs to a non-staff identity (e.g. a STUDENT) in Central Auth.

    Central Auth only allows staff identities (STAFF, FACILITATOR) to be provisioned or modified
    through this flow, so this is a conflict caused by the supplied email, not a service outage.
    """

    def __init__(self, message: str, existing_role: str | None = None):
        super().__init__(message)
        self.existing_role = existing_role


class CentralAuthReconciliationError(Exception):
    """Raised when an existing local user's Central Auth binding conflicts with the provisioned ID."""
    pass


_NON_STAFF_ROLE_RE = re.compile(r"role\s+'([A-Za-z_ ]+)'", re.IGNORECASE)


def _extract_non_staff_role(err_msg: str) -> str | None:
    """Return the existing role if Central Auth rejected the call because the identity is non-staff."""
    lowered = err_msg.lower()
    if "restricted to staff" not in lowered and "non-staff" not in lowered:
        return None
    match = _NON_STAFF_ROLE_RE.search(err_msg)
    return match.group(1).strip().upper() if match else "NON-STAFF"


async def provision_or_invite_staff(
    email: str,
    name: str,
    role: str = "STAFF",
    send_invite_email: bool = True,
) -> str:
    """
    Calls Central Auth (barabari-auth-service) to provision or link a staff user identity.
    Returns the centralUserId (UUID) on success.
    Raises CentralAuthProvisioningError if Central Auth is unconfigured, unreachable, or rejects the call.
    """
    base_url = (settings.AUTH_SERVICE_BASE_URL or "").strip()
    api_key = (settings.CENTRAL_PROFILE_API_KEY or "").strip()

    if not base_url or not api_key:
        logger.error(
            "[central-auth] Central Auth bridge is unconfigured (AUTH_SERVICE_BASE_URL or CENTRAL_PROFILE_API_KEY missing). "
            "Refusing to provision staff account without Central Auth."
        )
        raise CentralAuthProvisioningError(
            "Central Auth service is not configured (missing AUTH_SERVICE_BASE_URL or CENTRAL_PROFILE_API_KEY). "
            "Cannot provision staff member without Central Auth identity."
        )

    url = f"{base_url.rstrip('/')}/barabari-auth/api/auth/internal/v1/users/provision-or-invite"
    headers = {
        "X-Internal-Api-Key": api_key,
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
    except httpx.HTTPError as exc:
        logger.error(f"[central-auth] HTTP transport error during provisioning for {email}: {exc}")
        raise CentralAuthProvisioningError(
            f"Central Auth service unreachable at {url}: {exc}"
        ) from exc
    except Exception as exc:
        logger.error(f"[central-auth] Unexpected error communicating with Central Auth for {email}: {exc}")
        raise CentralAuthProvisioningError(
            f"Unexpected error communicating with Central Auth: {exc}"
        ) from exc

    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code not in (200, 201) or not body.get("isSuccess"):
        err_msg = body.get("errorMessage") or f"HTTP {resp.status_code}"
        logger.error(f"[central-auth] Provisioning failed for {email}: {err_msg}")
        if 400 <= resp.status_code < 500:
            existing_role = _extract_non_staff_role(err_msg)
            if existing_role is not None:
                raise CentralAuthNonStaffAccountError(
                    f"The email '{email.strip().lower()}' already belongs to a "
                    f"{existing_role.lower()} account, so it cannot be added as a team member. "
                    "Please use a different email address.",
                    existing_role=existing_role,
                ) from None
        raise CentralAuthProvisioningError(f"Central Auth rejected provisioning: {err_msg}")

    data = body.get("data") or {}
    central_user_id = data.get("centralUserId")
    if not central_user_id or not str(central_user_id).strip():
        logger.error(f"[central-auth] Central Auth response missing centralUserId for {email}: {body}")
        raise CentralAuthProvisioningError(
            "Central Auth provisioning response succeeded but did not return a centralUserId."
        )

    logger.info(
        f"[central-auth] Successfully provisioned/linked user {email} (isNewUser={data.get('isNewUser')}) -> centralUserId={central_user_id}"
    )
    return str(central_user_id)

