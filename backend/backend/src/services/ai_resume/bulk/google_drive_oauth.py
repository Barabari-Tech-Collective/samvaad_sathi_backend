"""Recruiter-consented Google Drive OAuth for private resume links."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from src.config.manager import settings

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_ABOUT_URL = (
    "https://www.googleapis.com/drive/v3/about?fields=user(emailAddress)"
)
DRIVE_SCOPES = ("https://www.googleapis.com/auth/drive.readonly",)


class DriveOAuthConfigurationError(RuntimeError):
    pass


class DriveOAuthStateError(ValueError):
    pass


@dataclass(frozen=True)
class GoogleDriveGrant:
    refresh_token: str
    email: str | None
    scopes: list[str]


def configuration_errors() -> list[str]:
    required = {
        "GOOGLE_DRIVE_OAUTH_CLIENT_ID": settings.GOOGLE_DRIVE_OAUTH_CLIENT_ID,
        "GOOGLE_DRIVE_OAUTH_CLIENT_SECRET": settings.GOOGLE_DRIVE_OAUTH_CLIENT_SECRET,
        "GOOGLE_DRIVE_OAUTH_REDIRECT_URI": settings.GOOGLE_DRIVE_OAUTH_REDIRECT_URI,
        "GOOGLE_DRIVE_OAUTH_FRONTEND_RETURN_URL": settings.GOOGLE_DRIVE_OAUTH_FRONTEND_RETURN_URL,
        "GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY": settings.GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY,
    }
    errors = [
        f"{name} is missing" for name, value in required.items() if not value.strip()
    ]
    if settings.GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY:
        try:
            Fernet(settings.GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY.encode("ascii"))
        except (ValueError, TypeError):
            errors.append("GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY is not a valid Fernet key")
    return errors


def is_configured() -> bool:
    return not configuration_errors()


def _require_configured() -> None:
    errors = configuration_errors()
    if errors:
        raise DriveOAuthConfigurationError("; ".join(errors))


def _state_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.SESSION_SECRET_KEY, salt="bulk-ats-google-drive-oauth-v1"
    )


def build_authorization_request(*, user_id: int) -> tuple[str, str]:
    _require_configured()
    state = _state_serializer().dumps(
        {"user_id": user_id, "nonce": secrets.token_urlsafe(24)}
    )
    query = urlencode(
        {
            "client_id": settings.GOOGLE_DRIVE_OAUTH_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_DRIVE_OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(DRIVE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
    )
    return f"{GOOGLE_AUTH_URL}?{query}", state


def build_authorization_url(*, user_id: int) -> str:
    return build_authorization_request(user_id=user_id)[0]


def decode_state(state: str) -> int:
    try:
        payload = _state_serializer().loads(
            state,
            max_age=settings.GOOGLE_DRIVE_OAUTH_STATE_MAX_AGE_SECONDS,
        )
        user_id = int(payload["user_id"])
        if not payload.get("nonce"):
            raise DriveOAuthStateError("OAuth state nonce is missing")
        return user_id
    except SignatureExpired as exc:
        raise DriveOAuthStateError("Google Drive connection request expired") from exc
    except (BadSignature, KeyError, TypeError, ValueError) as exc:
        raise DriveOAuthStateError("Google Drive connection state is invalid") from exc


async def exchange_authorization_code(*, code: str) -> GoogleDriveGrant:
    _require_configured()
    async with httpx.AsyncClient(timeout=30.0) as client:
        token_response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_DRIVE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_DRIVE_OAUTH_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_DRIVE_OAUTH_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        refresh_token = token_data.get("refresh_token")
        access_token = token_data.get("access_token")
        if not refresh_token or not access_token:
            raise ValueError(
                "Google did not return the required offline grant; reconnect and approve access"
            )

        userinfo_response = await client.get(
            GOOGLE_DRIVE_ABOUT_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        userinfo_response.raise_for_status()
        userinfo = userinfo_response.json()
        scopes = str(token_data.get("scope") or " ".join(DRIVE_SCOPES)).split()
        return GoogleDriveGrant(
            refresh_token=str(refresh_token),
            email=(userinfo.get("user") or {}).get("emailAddress"),
            scopes=scopes,
        )


def encrypt_refresh_token(refresh_token: str) -> str:
    _require_configured()
    return (
        Fernet(settings.GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY.encode("ascii"))
        .encrypt(refresh_token.encode("utf-8"))
        .decode("ascii")
    )


def decrypt_refresh_token(ciphertext: str, *, key_id: str) -> str:
    _require_configured()
    if key_id != settings.GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY_ID:
        raise DriveOAuthConfigurationError(
            f"Credential key {key_id!r} is unavailable; rotate it before removing the old key"
        )
    try:
        return (
            Fernet(settings.GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY.encode("ascii"))
            .decrypt(ciphertext.encode("ascii"))
            .decode("utf-8")
        )
    except InvalidToken as exc:
        raise DriveOAuthConfigurationError(
            "Stored Google Drive credential cannot be decrypted"
        ) from exc


async def refresh_access_token(*, encrypted_refresh_token: str, key_id: str) -> str:
    refresh_token = decrypt_refresh_token(encrypted_refresh_token, key_id=key_id)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.GOOGLE_DRIVE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_DRIVE_OAUTH_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
        access_token = response.json().get("access_token")
        if not access_token:
            raise ValueError("Google refresh response did not contain an access token")
        return str(access_token)


async def revoke_refresh_token(*, encrypted_refresh_token: str, key_id: str) -> None:
    refresh_token = decrypt_refresh_token(encrypted_refresh_token, key_id=key_id)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://oauth2.googleapis.com/revoke",
            data={"token": refresh_token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        # Google returns 200 for successful revocation and 400 when a token is
        # already invalid. Both are safe states for local deletion.
        if response.status_code not in {200, 400}:
            response.raise_for_status()
