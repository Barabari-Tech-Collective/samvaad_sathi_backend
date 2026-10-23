import logging
import httpx
from src.config.manager import settings

logger = logging.getLogger(__name__)


class CentralAuthProvisioningError(Exception):
    """Raised when staff provisioning or invitation in Central Auth fails, is misconfigured, or unreachable."""
    pass


class CentralAuthReconciliationError(Exception):
    """Raised when an existing local user's Central Auth binding conflicts with the provisioned ID."""
    pass


class ProvisionResult(str):
    """
    Subclasses str to preserve backward compatibility with str expectations
    while providing structured metadata like is_new_user and previous_role for saga compensation.
    """
    central_user_id: str
    is_new_user: bool
    role_reconciled: bool
    previous_role: str | None
    password_updated: bool
    previous_password_hash: str | None
    previous_must_set_password: bool | None

    def __new__(
        cls,
        central_user_id: str,
        is_new_user: bool = False,
        role_reconciled: bool = False,
        previous_role: str | None = None,
        password_updated: bool = False,
        previous_password_hash: str | None = None,
        previous_must_set_password: bool | None = None,
        **kwargs,
    ):
        instance = super().__new__(cls, central_user_id)
        instance.central_user_id = str(central_user_id)
        instance.is_new_user = bool(is_new_user)
        instance.role_reconciled = bool(role_reconciled)
        instance.previous_role = previous_role
        instance.password_updated = bool(password_updated)
        instance.previous_password_hash = previous_password_hash
        instance.previous_must_set_password = previous_must_set_password
        return instance


def derive_central_role(access_role: str | None) -> str:
    """
    Derives canonical Central Auth role ('FACILITATOR' or 'STAFF') from team member access_role.
    """
    if access_role and "facilitator" in access_role.lower():
        return "FACILITATOR"
    return "STAFF"


async def provision_or_invite_staff(
    email: str,
    name: str,
    role: str = "STAFF",
    send_invite_email: bool = True,
    password: str | None = None,
) -> ProvisionResult:
    """
    Calls Central Auth (barabari-auth-service) to provision or link a staff user identity.
    Returns ProvisionResult (UUID str subclass with is_new_user and previous_role) on success.
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
        "X-Product-Unique-Id": settings.SAMPARK_PRODUCT_UNIQUE_ID,
        "Content-Type": "application/json",
    }
    payload = {
        "email": email.strip().lower(),
        "name": name.strip(),
        "productUniqueId": settings.SAMPARK_PRODUCT_UNIQUE_ID,
        "role": role.strip().upper() if role else "STAFF",
        "sendInviteEmail": send_invite_email,
    }
    if password and password.strip():
        payload["password"] = password.strip()

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
        raise CentralAuthProvisioningError(f"Central Auth rejected provisioning: {err_msg}")

    data = body.get("data") or {}
    central_user_id = data.get("centralUserId")
    if not central_user_id or not str(central_user_id).strip():
        logger.error(f"[central-auth] Central Auth response missing centralUserId for {email}: {body}")
        raise CentralAuthProvisioningError(
            "Central Auth provisioning response succeeded but did not return a centralUserId."
        )

    is_new_user = bool(data.get("isNewUser", False))
    role_reconciled = bool(data.get("roleReconciled", False))
    previous_role = data.get("previousRole") or data.get("role")
    password_updated = bool(data.get("passwordUpdated", False))
    previous_password_hash = data.get("previousPasswordHash")
    previous_must_set_password = data.get("previousMustSetPassword")
    logger.info(
        f"[central-auth] Successfully provisioned/linked user {email} (isNewUser={is_new_user}) -> centralUserId={central_user_id}"
    )
    return ProvisionResult(
        central_user_id=str(central_user_id),
        is_new_user=is_new_user,
        role_reconciled=role_reconciled,
        previous_role=previous_role,
        password_updated=password_updated,
        previous_password_hash=previous_password_hash,
        previous_must_set_password=previous_must_set_password,
    )


async def revoke_provisioning(
    email: str,
    is_new_user: bool = False,
    central_user_id: str | None = None,
    previous_role: str | None = None,
    previous_password_hash: str | None = None,
    previous_must_set_password: bool | None = None,
) -> bool:
    """
    Calls Central Auth to perform compensating revocation for a provisioned user identity.
    - If is_new_user=True: Central Auth removes product entitlement and deletes newly created user.
    - If is_new_user=False: Central Auth PRESERVES product entitlement and reverts previous role/password.
    If is_new_user=False and no state changes need reversion, refrains from calling to preserve existing access.
    """
    if not is_new_user and not (previous_role or previous_password_hash or previous_must_set_password is not None):
        logger.warning(
            f"[central-auth] Refusing compensating revocation for existing user {email} without state changes. "
            "Pre-existing access and product entitlements must never be revoked on local failure."
        )
        return False

    base_url = (settings.AUTH_SERVICE_BASE_URL or "").strip()
    api_key = (settings.CENTRAL_PROFILE_API_KEY or "").strip()

    if not base_url or not api_key:
        logger.warning(
            "[central-auth] Cannot execute compensating revocation: Central Auth bridge is unconfigured."
        )
        return False

    url = f"{base_url.rstrip('/')}/barabari-auth/api/auth/internal/v1/users/revoke-provisioning"
    headers = {
        "X-Internal-Api-Key": api_key,
        "X-Product-Unique-Id": settings.SAMPARK_PRODUCT_UNIQUE_ID,
        "Content-Type": "application/json",
    }
    payload = {
        "email": email.strip().lower(),
        "productUniqueId": settings.SAMPARK_PRODUCT_UNIQUE_ID,
        "isNewUser": is_new_user,
    }
    if central_user_id:
        payload["centralUserId"] = str(central_user_id).strip()
    if previous_role:
        payload["previousRole"] = str(previous_role).strip()
    if previous_password_hash:
        payload["previousPasswordHash"] = str(previous_password_hash).strip()
    if previous_must_set_password is not None:
        payload["previousMustSetPassword"] = bool(previous_must_set_password)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except Exception as exc:
        logger.error(f"[central-auth] Compensating revocation HTTP error for {email}: {exc}")
        return False

    try:
        body = resp.json() if hasattr(resp, "json") else {}
    except Exception:
        body = {}

    if getattr(resp, "status_code", 200) not in (200, 204) or not body.get("isSuccess", True):
        logger.error(f"[central-auth] Compensating revocation failed for {email}: {body}")
        return False

    logger.info(f"[central-auth] Compensating revocation succeeded for {email}")
    return True


async def compensate_central_role(
    email: str,
    name: str,
    previous_canonical_role: str,
) -> bool:
    """
    Compensates a role change in Central Auth by reverting the user's role to previous_canonical_role.
    Executed as part of a saga rollback when subsequent local database operations fail.
    """
    base_url = (settings.AUTH_SERVICE_BASE_URL or "").strip()
    api_key = (settings.CENTRAL_PROFILE_API_KEY or "").strip()

    if not base_url or not api_key:
        logger.warning(
            "[central-auth] Cannot execute role compensation: Central Auth bridge is unconfigured."
        )
        return False

    try:
        await provision_or_invite_staff(
            email=email,
            name=name,
            role=previous_canonical_role,
            send_invite_email=False,
            password=None,
        )
        logger.info(
            f"[central-auth] Successfully compensated Central Auth role back to {previous_canonical_role} for {email}"
        )
        return True
    except Exception as exc:
        logger.error(
            f"[central-auth] CRITICAL: Failed to compensate Central Auth role back to {previous_canonical_role} for {email}: {exc}"
        )
        return False


_CRED_RECONCILE_KEY_PREFIX = "SSO_CRED_RECONCILE:"
_RECONCILIATION_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def _cred_reconcile_key(email: str) -> str:
    return f"{_CRED_RECONCILE_KEY_PREFIX}{email.strip().lower()}"


async def record_durable_credential_reconciliation(
    user_id: int | None,
    email: str,
    new_password_hash: str,
    central_user_id: str | None = None,
    async_session=None,
) -> None:
    """
    Durable reconciliation path for credentials when Central Auth succeeds but local commit fails.
    1. Persists durable reconciliation state in Redis.
    2. Invalidates active sessions/tokens to prevent usage of stale credentials.
    3. Reconciles User.password_hash in the local database in an isolated transaction.
    """
    import json
    import datetime
    from src.repository.redis_client import async_redis
    from src.securities.authorizations.access_revocation import set_access_revoked

    email_clean = email.strip().lower()
    record = {
        "user_id": user_id,
        "email": email_clean,
        "password_hash": new_password_hash,
        "central_user_id": str(central_user_id) if central_user_id else None,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "pending_local_sync",
    }

    # 1. Persist to Redis
    try:
        if async_redis.client:
            await async_redis.client.set(
                _cred_reconcile_key(email_clean),
                json.dumps(record),
                ex=_RECONCILIATION_TTL_SECONDS,
            )
            logger.info(
                f"[reconciliation] Persisted durable credential reconciliation record in Redis for {email_clean}"
            )
    except Exception as redis_err:
        logger.error(
            f"[reconciliation] Failed to write reconciliation record to Redis for {email_clean}: {redis_err}"
        )

    # 2. Invalidate active tokens to prevent credential/session split-brain
    if central_user_id:
        try:
            await set_access_revoked(str(central_user_id).strip())
            logger.info(f"[reconciliation] Invalidated active sessions for {central_user_id}")
        except Exception as revoke_err:
            logger.error(
                f"[reconciliation] Failed to revoke active session for {central_user_id}: {revoke_err}"
            )

    # 3. Direct isolated local database reconciliation
    if async_session is not None and user_id is not None:
        try:
            from sqlalchemy import update
            from src.models.db.user import User

            stmt = update(User).where(User.id == user_id).values(password_hash=new_password_hash)
            await async_session.execute(stmt)
            await async_session.commit()
            logger.info(
                f"[reconciliation] Successfully reconciled password_hash in local DB for user_id={user_id}"
            )
        except Exception as db_err:
            logger.warning(
                f"[reconciliation] Could not write isolated DB password update for user_id={user_id}: {db_err}. "
                "Pending Redis reconciliation record will be applied upon next sign-in."
            )


async def check_and_apply_credential_reconciliation(
    user,
    password: str,
    async_session,
) -> bool:
    """
    Called by UserCRUDRepository.verify_password when local password authentication fails.
    Checks if a durable credential reconciliation record exists in Redis for this user.
    If candidate password matches the reconciled hash, it updates User.password_hash,
    commits it locally, clears the reconciliation record, and returns True.
    """
    import json
    from src.repository.redis_client import async_redis
    from src.securities.hashing.password import pwd_generator

    if not user or not getattr(user, "email", None):
        return False

    email_clean = user.email.strip().lower()
    key = _cred_reconcile_key(email_clean)

    try:
        if not async_redis.client:
            return False
        raw_data = await async_redis.client.get(key)
        if not raw_data:
            return False

        data = json.loads(raw_data)
        reconciled_hash = data.get("password_hash")
        if not reconciled_hash:
            return False

        # Verify candidate password against reconciled hash
        if pwd_generator.is_password_authenticated(
            hash_salt="", password=password, hashed_password=reconciled_hash
        ):
            user.password_hash = reconciled_hash
            await async_session.commit()
            await async_session.refresh(user)
            await async_redis.client.delete(key)
            logger.info(
                f"[reconciliation] Applied pending credential reconciliation for {email_clean} upon successful login."
            )
            return True
    except Exception as exc:
        logger.error(f"[reconciliation] Error during reconciliation check for {email_clean}: {exc}")
        return False

    return False

