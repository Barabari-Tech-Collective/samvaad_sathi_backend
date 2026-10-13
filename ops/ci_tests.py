"""Run tests with synthetic settings. No production credentials are needed."""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
settings = {
    "ENVIRONMENT": "DEV", "BACKEND_SERVER_HOST": "127.0.0.1", "BACKEND_SERVER_PORT": "8000",
    "BACKEND_SERVER_WORKERS": "1", "POSTGRES_HOST": "127.0.0.1", "POSTGRES_DB": "samvaad_ci",
    "POSTGRES_PASSWORD": "ci-only-password", "POSTGRES_USERNAME": "ci", "POSTGRES_PORT": "5432",
    "POSTGRES_SCHEMA": "public", "DB_MAX_POOL_CON": "5", "DB_POOL_SIZE": "2", "DB_POOL_OVERFLOW": "1",
    "DB_TIMEOUT": "5", "IS_DB_ECHO_LOG": "False", "IS_DB_FORCE_ROLLBACK": "True",
    "IS_DB_EXPIRE_ON_COMMIT": "False", "API_TOKEN": "ci-only", "AUTH_TOKEN": "ci-only",
    "JWT_TOKEN_PREFIX": "Bearer", "JWT_SECRET_KEY": "ci-only-not-a-deployment-secret",
    "JWT_SUBJECT": "access", "JWT_MIN": "60", "JWT_HOUR": "24", "JWT_DAY": "1",
    "IS_ALLOWED_CREDENTIALS": "True", "HASHING_ALGORITHM_LAYER_1": "bcrypt",
    "HASHING_ALGORITHM_LAYER_2": "argon2", "HASHING_SALT": "ci-only",
    "JWT_ALGORITHM": "HS256", "SESSION_SECRET_KEY": "ci-only-not-a-deployment-secret",
    "OPENAI_API_KEY": "ci-only", "LOKI_URL": "", "RABBITMQ_URL": "",
    # Managed Postgres requires TLS, so DB_SSL_ENABLED defaults to true. The CI
    # service container speaks plaintext and rejects the upgrade, which failed
    # the migration gate before it could execute a single statement.
    "DB_SSL_ENABLED": "False",
}
os.environ.update(settings)
sys.path.insert(0, str(ROOT / "backend/backend"))

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([str(ROOT / "backend/backend/tests"), *sys.argv[1:]]))
