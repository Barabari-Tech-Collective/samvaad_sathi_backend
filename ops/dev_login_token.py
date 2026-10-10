"""Mint a local sign-in token so the app can be exercised without auth-service.

auth-service issues HS256 tokens signed with AUTH_SERVICE_JWT_SECRET, and this
backend ships the same documented local-dev default, so a token minted here
verifies exactly as a real one does. That makes a full authenticated walkthrough
possible on a laptop without running auth-service, its database, Redis,
RabbitMQ and Eureka.

Refuses to run outside ENVIRONMENT=DEV, and refuses to run against a database
that is not on this machine, so it can never mint access to a shared system.

    python ops/dev_login_token.py                      # default dev student
    python ops/dev_login_token.py --email me@test.dev  # a specific account
"""

from __future__ import annotations

import argparse
import base64
import datetime
import pathlib
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/backend"))

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
TOKEN_LIFETIME = datetime.timedelta(hours=12)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default="dev.student@samvaad.local")
    parser.add_argument("--name", default="Dev Student")
    parser.add_argument("--role", default="STUDENT")
    arguments = parser.parse_args()

    from jose import jwt as jose_jwt
    from sqlalchemy import create_engine, text

    from src.config.manager import settings

    if settings.ENVIRONMENT != "DEV":
        print(f"Refusing to run: ENVIRONMENT is {settings.ENVIRONMENT}, not DEV.")
        return 2
    if settings.DB_POSTGRES_HOST not in LOCAL_HOSTS:
        print(f"Refusing to run: database host is {settings.DB_POSTGRES_HOST}, not this machine.")
        return 2

    # Mirrors src/repository/database.py: the password is URL-encoded, so a
    # character such as @ or / in it cannot corrupt the connection string.
    password = urllib.parse.quote_plus(settings.DB_POSTGRES_PASSWORD)
    engine = create_engine(
        f"postgresql+psycopg2://{settings.DB_POSTGRES_USERNAME}:{password}"
        f"@{settings.DB_POSTGRES_HOST}:{settings.DB_POSTGRES_PORT}/{settings.DB_POSTGRES_NAME}"
    )
    with engine.begin() as connection:
        existing = connection.execute(
            text('SELECT id FROM "user" WHERE email = :email'), {"email": arguments.email}
        ).first()
        if existing:
            user_id = existing[0]
            print(f"Using existing user {arguments.email} (id={user_id})")
        else:
            user_id = connection.execute(
                text(
                    'INSERT INTO "user" (email, name, password_hash) '
                    "VALUES (:email, :name, :password_hash) RETURNING id"
                ),
                {
                    "email": arguments.email,
                    "name": arguments.name,
                    # Local sign-in is by minted token; no password path is enabled.
                    "password_hash": "!dev-no-password-login",
                },
            ).scalar_one()
            print(f"Created user {arguments.email} (id={user_id})")

    issued_at = datetime.datetime.now(tz=datetime.timezone.utc)
    token = jose_jwt.encode(
        {
            "sub": arguments.email,
            "role": arguments.role,
            "userUniqueId": f"dev-{user_id}",
            "iat": issued_at,
            "exp": issued_at + TOKEN_LIFETIME,
        },
        key=base64.b64decode(settings.AUTH_SERVICE_JWT_SECRET),
        algorithm="HS256",
    )

    print("\nToken (valid 12h):\n")
    print(token)
    print("\nTo sign in: open http://localhost:3000, then run this in the browser console:\n")
    print(f'  document.cookie = "token={token}; path=/";')
    print("\nThen reload. Verify from a terminal with:\n")
    print(f'  curl -s -H "Authorization: Bearer {token[:24]}..." http://localhost:8000/api/users/me')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
