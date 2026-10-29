"""Persistent local test stack. Run with the backend .venv Python: start/status/stop.

Existing source .env files are not edited. Only AI/speech settings are imported.
All databases, identity secrets, queues and storage are dedicated local resources.
"""
from __future__ import annotations
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DESKTOP = ROOT.parent
STATE = DESKTOP / "samvaad-local-test"
DOCKER = ["/opt/homebrew/bin/docker-compose", "-f", str(HERE / "compose.yml")]
PROVIDER_KEYS = "OPENAI_API_KEY OPENAI_MODEL LLM_PROVIDER DEEPSEEK_API_KEY DEEPSEEK_BASE_URL DEEPSEEK_MODEL GROQ_API_KEY GROQ_BASE_URL GROQ_WHISPER_MODEL STT_PROVIDER ELEVENLABS_API_KEY ELEVENLABS_VOICE_ID JEV_API_KEY JEV_API_BASE_URL JEV_MODEL".split()


def private_json(name, value):
    target = STATE / name
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2)
    return target


def clean_env():
    return {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG") if k in os.environ} | {"AWS_EC2_METADATA_DISABLED":"true", "DOTENV_CONFIG_QUIET":"true", "DOCKER_CONTEXT":"colima-samvaad-local"}


def run(command, env, label, cwd=STATE):
    with (STATE / (label + ".log")).open("w") as log:
        result = subprocess.run(list(map(str, command)), env=env, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"{label} failed; inspect private log {STATE / (label + '.log')}")


def launch(command, env, label, cwd=STATE):
    with (STATE / (label + ".log")).open("a") as log:
        child = subprocess.Popen(list(map(str, command)), env=env, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    processes = json.loads((STATE / "processes.json").read_text()) if (STATE / "processes.json").exists() else {}
    processes[label] = child.pid
    private_json("processes.json", processes)


def wait_url(url, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f"Local service not ready: {url}; inspect {STATE} logs")


def configuration():
    STATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(STATE, 0o700)
    if not (STATE / "cert.pem").exists():
        run(["openssl","req","-x509","-newkey","rsa:2048","-nodes","-days","30","-subj","/CN=localhost","-addext","subjectAltName=DNS:localhost,IP:127.0.0.1","-keyout",STATE / "key.pem","-out",STATE / "cert.pem"],clean_env(),"local-certificate")
    import certifi
    (STATE / "ca-bundle.pem").write_bytes(Path(certifi.where()).read_bytes() + b"\n" + (STATE / "cert.pem").read_bytes())
    if (STATE / "local-secrets.json").exists():
        local = json.loads((STATE / "local-secrets.json").read_text())
    else:
        local = {name: secrets.token_urlsafe(30) for name in ("db", "broker", "storage", "password", "machine", "sync", "session")}
        local["jwt"] = base64.b64encode(secrets.token_bytes(48)).decode()
        private_json("local-secrets.json", local)
    if "storageMasterKey" not in local:
        local["storageMasterKey"] = base64.b64encode(secrets.token_bytes(32)).decode()
        private_json("local-secrets.json", local)
    from dotenv import dotenv_values
    template = {k: v for k, v in dotenv_values(ROOT / "backend/.env.example").items() if v is not None}
    providers = json.loads((STATE / "production-providers.json").read_text()) if (STATE / "production-providers.json").exists() else dotenv_values(ROOT / "backend/backend/.env")
    template.update({key: providers[key] or "" for key in PROVIDER_KEYS if key in providers})
    if not providers.get("LLM_PROVIDER") and providers.get("DEEPSEEK_API_KEY"):
        template["LLM_PROVIDER"] = "deepseek"
    template.update({
        "ENVIRONMENT":"DEV", "BACKEND_SERVER_HOST":"127.0.0.1", "BACKEND_SERVER_PORT":"8000", "BACKEND_SERVER_WORKERS":"1",
        "POSTGRES_HOST":"127.0.0.1", "POSTGRES_PORT":"15432", "POSTGRES_DB":"samvaad_local", "POSTGRES_USERNAME":"samvaad_local", "POSTGRES_PASSWORD":local["db"], "DB_SSL_ENABLED":"False", "IS_DB_FORCE_ROLLBACK":"False", "IS_DB_ECHO_LOG":"False", "SESSION_SECRET_KEY":local["session"],
        "AUTH_SERVICE_BASE_URL":"https://localhost:8441", "AUTH_SERVICE_PUBLIC_BASE_URL":"http://localhost:8081", "SSL_CERT_FILE":str(STATE / "ca-bundle.pem"), "AUTH_SERVICE_JWT_SECRET":local["jwt"], "JWT_SECRET_KEY":local["session"], "SSO_REQUIRED_ROLE":"", "SSO_ALLOWED_ROLES":"STUDENT,FACILITATOR,ADMIN,SUPER_ADMIN,PROGRAM_MANAGER,PLACEMENT_COM",
        "SSO_REDIRECT_URI":"http://localhost:8000/api/auth/sso/callback", "COGNITO_POST_LOGIN_REDIRECT_URL":"http://localhost:3000/auth/signup", "COGNITO_POST_LOGOUT_REDIRECT_URL":"http://localhost:3000/auth/signup",
        "CENTRAL_IDENTITY_CHECK_ENABLED":"True", "PRODUCT_IDENTITY_API_KEY":local["machine"], "CENTRAL_PROFILE_API_KEY":local["machine"],
        "REDIS_HOST":"127.0.0.1", "REDIS_PORT":"16379", "REDIS_DB":"0", "REDIS_PASSWORD":"", "REDIS_URL":"",
        "RABBITMQ_ENABLED":"True", "RABBITMQ_HOST":"127.0.0.1", "RABBITMQ_PORT":"15672", "RABBITMQ_USERNAME":"samvaad_local", "RABBITMQ_PASSWORD":local["broker"], "RABBITMQ_SSL_ENABLED":"False", "RABBITMQ_VIRTUAL_HOST":"/", "RABBITMQ_ACCESS_REVOKED_EXCHANGE":"barabari.user-access-revoked-exchange-local", "RABBITMQ_ACCESS_REVOKED_ROUTING_KEY":"barabari.user-access-revoked-routing-key-local", "RABBITMQ_ACCESS_REVOKED_QUEUE":"samvaad-local-access-revoked",
        "AWS_ACCESS_KEY_ID":"samvaad-local", "AWS_SECRET_ACCESS_KEY":local["storage"], "AWS_SESSION_TOKEN":"", "AWS_REGION":"ap-south-1", "AWS_S3_BUCKET_NAME":"samvaad-local-files", "AWS_S3_LOCAL_ENDPOINT_URL":"http://127.0.0.1:19000", "AWS_S3_PUBLIC_BASE_URL":"http://127.0.0.1:19000/samvaad-local-files",
        "SAMPARK_ENROLLMENT_SYNC_ENABLED":"True", "SAMPARK_ENROLLMENT_SYNC_KEY":local["sync"], "SAMPARK_ENROLLMENT_SYNC_URL":"http://127.0.0.1:8082/sampark/internal/samvaad-enrollment-snapshots",
        "SAMPARK_SAATHI_BASE_URL":"http://127.0.0.1:8082", "SAMPARK_SAATHI_API_KEY":local["machine"], "SUPER_ADMIN_API_KEY":local["machine"], "ADMIN_EMAILS":"", "SUPER_ADMIN_EMAILS":"", "LOKI_URL":"", "LOKI_USERNAME":"", "LOKI_API_KEY":"", "TRUST_PROXY_HEADERS":"False", "EXPOSE_API_DOCS":"True",
        "GOOGLE_DRIVE_OAUTH_CLIENT_ID":"", "GOOGLE_DRIVE_OAUTH_CLIENT_SECRET":"", "GOOGLE_DRIVE_TOKEN_ENCRYPTION_KEY":"", "GOOGLE_DRIVE_OAUTH_REDIRECT_URI":"", "MOCK_TTS":"False",
    })
    # Jev remains behind existing release gates; local setup must not approve a rollout.
    central = {
        "APP_PROFILE":"local", "SPRING_DATASOURCE_URL":f"postgresql://samvaad_local:{local['db']}@127.0.0.1:15432/samvaad_local", "SPRING_DATASOURCE_SSL_ENABLED":"false", "SPRING_JPA_SHOW_SQL":"false", "SPRING_DATASOURCE_MINIMUM_IDLE":"1", "SPRING_DATASOURCE_MAXIMUM_POOL_SIZE":"5",
        "REDIS_HOST":"127.0.0.1", "REDIS_PORT":"16379", "REDIS_USERNAME":"default", "REDIS_PASSWORD":"", "REDIS_SSL_ENABLED":"false",
        "RABBITMQ_HOST":"127.0.0.1", "RABBITMQ_PORT":"15672", "RABBITMQ_USERNAME":"samvaad_local", "RABBITMQ_PASSWORD":local["broker"], "RABBITMQ_SSL_ENABLED":"false", "RABBITMQ_VIRTUAL_HOST":"/",
        "JWT_SECRET":local["jwt"], "EUREKA_CLIENT_ENABLED":"false", "SAATHI_SERVICE_DIRECT_URL":"http://127.0.0.1:8082", "SECURITY_ALLOWED_ORIGINS":"http://localhost:3000,http://localhost:3001,http://localhost:8000",
        "PUBLIC_BASE_URL":"http://localhost:8081", "SSO_LOGIN_PAGE_URL":"http://localhost:3001/student-login", "SSO_ALLOWED_REDIRECT_URIS":"81c53f68-35f4-4133-9e35-06f5c30354b785=http://localhost:8000/api/auth/sso/callback", "SAMPARK_PRODUCT_UNIQUE_ID":template["SAMPARK_PRODUCT_UNIQUE_ID"],
        "CENTRAL_PROFILE_API_KEY":local["machine"], "PRODUCT_IDENTITY_API_KEY":local["machine"], "AUTH_SERVICE_BASE_URL":"http://127.0.0.1:8081", "SAMVAAD_SAATHI_BASE_URL":"http://127.0.0.1:8000", "SAMVAAD_SAATHI_SUPER_ADMIN_API_KEY":local["machine"], "SAMVAAD_SAATHI_AUTH_API_KEY":local["machine"], "SAMPARK_ENROLLMENT_SYNC_KEY":local["sync"],
        "LOCAL_TEST_PASSWORD":local["password"], "LOCAL_TLS_KEY":str(STATE / "key.pem"), "LOCAL_TLS_CERT":str(STATE / "cert.pem"), "LOKI_URL":"", "GOOGLE_OAUTH_CLIENT_ID":"", "SMTP_HOST":"127.0.0.1", "SMTP_PORT":"11025", "SMTP_LOGIN":"local-only", "SMTP_PASSWORD":local["session"], "EMAIL_FROM_ADDRESS":"no-reply@example.com", "EMAIL_FROM_NAME":"Samvaad Local Test", "FEIGN_AUTH_API_KEY":local["machine"], "FEIGN_AUTH_EXPECTED_APP_NAME":"local", "AWS_ACCESS_KEY":"local", "AWS_SECRET_KEY":local["storage"], "AWS_S3_BUCKET_NAME":"samvaad-local-files", "AWS_S3_ENDPOINT":"http://127.0.0.1:19000", "AWS_REGION":"ap-south-1",
    }
    private_json("product.json", template)
    private_json("auth.json", central | {"PORT":"8081"})
    private_json("sampark.json", central | {"PORT":"8082"})
    return local


def start():
    if (STATE / "processes.json").exists():
        raise RuntimeError("Stack state exists; use status or stop before starting again")
    for port in (15432, 16379, 15672, 19000, 11025, 18025, 8000, 8081, 8082, 8441, 3000, 3001):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1",port)) == 0:
                raise RuntimeError(f"Port {port} is occupied; no existing process was stopped")
    local = configuration()
    env = clean_env()
    compose_env = env | {"LOCAL_DB_PASSWORD":local["db"], "LOCAL_BROKER_PASSWORD":local["broker"], "LOCAL_STORAGE_PASSWORD":local["storage"], "LOCAL_STORAGE_MASTER_KEY":local["storageMasterKey"]}
    run(DOCKER + ["up", "-d", "--wait", "--wait-timeout", "180"], compose_env, "dependencies")
    central = DESKTOP / "Sampark-Saathi-Backend"
    for service, config in (("auth-service","auth.json"),("sampark-saathi-service","sampark.json")):
        run(["node", HERE / "central.cjs", STATE / config, central, service, "migrate"], env, service + "-migrations")
    for mode in ("migrate", "storage"):
        run([sys.executable, HERE / "product.py", STATE / "product.json", mode], env, "product-"+mode)
    run([sys.executable, HERE / "seed.py", STATE / "product.json"], env, "product-seed")
    for service, config in (("auth-service","auth.json"),("sampark-saathi-service","sampark.json")):
        launch(["node", HERE / "central.cjs", STATE / config, central, service, "serve"], env, service)
    for mode in ("api","worker"):
        launch([sys.executable, HERE / "product.py", STATE / "product.json", mode], env, mode)
    frontend_env = env | {"NEXT_PUBLIC_API_BASE_URL":"http://localhost:8000/api", "NEXT_PUBLIC_APP_URL":"http://localhost:3000", "AUTH_BASE_URL":"http://localhost:8081", "NEXT_PUBLIC_POSTHOG_KEY":"", "NEXT_PUBLIC_POSTHOG_HOST":""}
    launch(["node", "node_modules/next/dist/bin/next", "dev", "--hostname", "127.0.0.1", "--port", "3000"], frontend_env, "student", DESKTOP / "samvaad-saathi-frontend")
    dashboard_env = env | {"NEXT_PUBLIC_API_URL":"/api", "AUTH_SERVICE_ORIGIN":"http://127.0.0.1:8081", "SAATHI_SERVICE_ORIGIN":"http://127.0.0.1:8081", "NOTIFICATION_SERVICE_ORIGIN":"http://127.0.0.1:18083", "SAMVAAD_SERVICE_ORIGIN":"http://127.0.0.1:8000", "NEXT_PUBLIC_SAMVAAD_API_URL":"http://localhost:8000/api", "NEXT_PUBLIC_ADMIN_BASE_URL":"http://localhost:3001", "NEXT_PUBLIC_SAMVAAD_SAATHI_URL":"http://localhost:3000", "NEXT_PUBLIC_CODEGURU_URL":"http://localhost:3001", "NEXT_PUBLIC_GOOGLE_OAUTH_CLIENT_ID":""}
    launch(["node", "node_modules/next/dist/bin/next", "dev", "--hostname", "127.0.0.1", "--port", "3001"], dashboard_env, "dashboard", DESKTOP / "sampark-saathi frontend")
    for url in ("http://localhost:8000/api/health", "http://localhost:8081/barabari-auth/actuator/health", "http://localhost:8082/sampark/actuator/health", "http://localhost:3000/auth/signup", "http://localhost:3001/login"):
        wait_url(url)
    print("Local stack running. Student http://localhost:3000 ; Dashboard http://localhost:3001/login")
    print("Test account credentials are in " + str(STATE / "TESTING.md"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start","status","stop"])
    action = parser.parse_args().action
    if action == "start":
        start()
    elif action == "status":
        process_file = STATE / "processes.json"
        if not process_file.exists():
            print("Local stack is not running. Start it with: .venv/bin/python ops/local-test/manage.py start")
            return
        for label, pid in json.loads(process_file.read_text()).items():
            try:
                os.kill(pid, 0)
                print(label + ": process running")
            except PermissionError:
                # EPERM still proves the PID exists; restricted shells may not
                # be allowed to signal a process started outside their sandbox.
                print(label + ": process running")
            except ProcessLookupError:
                print(label + ": stopped")
    else:
        pids = STATE / "processes.json"
        if pids.exists():
            for pid in json.loads(pids.read_text()).values():
                # Only terminate the recorded local bootstrap/Next process groups.
                info = subprocess.run(["ps","-p",str(pid),"-o","command="], capture_output=True,text=True).stdout
                if str(HERE) in info or "node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 300" in info:
                    os.killpg(pid, signal.SIGTERM)
            pids.unlink()
        secrets_file = STATE / "local-secrets.json"
        if not secrets_file.exists():
            print("Local stack is not running; there is no local state to stop")
            return
        local = json.loads(secrets_file.read_text())
        run(DOCKER + ["stop"], clean_env() | {"LOCAL_DB_PASSWORD":local["db"], "LOCAL_BROKER_PASSWORD":local["broker"], "LOCAL_STORAGE_PASSWORD":local["storage"], "LOCAL_STORAGE_MASTER_KEY":local.get("storageMasterKey", "not-yet-initialized")}, "stop-dependencies")
        print("Stopped local stack; test data preserved")


if __name__ == "__main__":
    main()
