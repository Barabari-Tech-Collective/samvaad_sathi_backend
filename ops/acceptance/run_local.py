"""Run repeatable local cross-service acceptance against disposable fixtures only."""

import argparse
import base64
import json
import os
import pathlib
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(
    description="Disposable local cross-service acceptance; requires PostgreSQL tools, Redis, OpenSSL and installed Node/Python dependencies."
)
parser.add_argument("--sampark-checkout", type=pathlib.Path, required=True)
parser.add_argument("--pg-bin", type=pathlib.Path, required=True)
parser.add_argument("--report", type=pathlib.Path, required=True)
args = parser.parse_args()
if not __debug__:
    parser.error(
        "Run without Python optimization; acceptance assertions must remain enabled"
    )
CENTRAL = args.sampark_checkout.resolve()
PG = args.pg_bin.resolve()
PYTHON = pathlib.Path(sys.executable)
for filename in [
    CENTRAL / "auth-service/src/config/dataSource.ts",
    CENTRAL / "sampark-saathi-service/src/config/dataSource.ts",
    PG / "initdb",
    PG / "pg_ctl",
]:
    if not filename.is_file():
        parser.error("Missing local source/runtime prerequisite")
if not shutil.which("redis-server") or not shutil.which("openssl"):
    parser.error("redis-server and openssl must be on PATH")


def free_port():
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return candidate.getsockname()[1]


work = pathlib.Path(tempfile.mkdtemp(prefix="ss_acceptance_"))
name = "ss_acceptance_" + secrets.token_hex(4)
ports = set()
while len(ports) < 4:
    ports.add(free_port())
pgport, redisport, centralport, apiport = sorted(ports)
processes = []
handles = []
started = False
os.umask(0o077)


def cmd(args, **kwargs):
    return subprocess.run(
        list(map(str, args)),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
        **kwargs,
    )


def sql(db, query, params=()):
    import psycopg2

    with psycopg2.connect(
        host="127.0.0.1", port=pgport, user="ss_acceptance", dbname=db
    ) as con:
        with con.cursor() as cur:
            cur.execute(query, params)
            if cur.description:
                return cur.fetchall()


def launch(args, env, label, cwd):
    log = (work / (label + ".log")).open("w")
    handles.append(log)
    p = subprocess.Popen(
        list(map(str, args)), env=env, cwd=cwd, stdout=log, stderr=subprocess.STDOUT
    )
    processes.append(p)
    return p


def http(url, token=None, body=None):
    req = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": "Bearer " + token} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(
            req,
            timeout=20,
            context=ssl.create_default_context(cafile=str(work / "cert.pem")),
        ) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        status = e.code
        e.close()
        return status, None


try:
    cmd(
        [
            PG / "initdb",
            "-D",
            work / "pg",
            "-U",
            "ss_acceptance",
            "-A",
            "trust",
            "--locale=C",
        ]
    )
    cmd(
        [
            PG / "pg_ctl",
            "-D",
            work / "pg",
            "-l",
            work / "postgres.log",
            "-o",
            f"-h 127.0.0.1 -p {pgport} -k {work}",
            "start",
        ]
    )
    started = True
    for suffix in ["central", "product"]:
        cmd(
            [
                PG / "createdb",
                "-h",
                "127.0.0.1",
                "-p",
                pgport,
                "-U",
                "ss_acceptance",
                name + "_" + suffix,
            ]
        )
    cmd(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-keyout",
            work / "key.pem",
            "-out",
            work / "cert.pem",
        ]
    )
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")
        if key in os.environ
    }
    sys.path.insert(0, str(ROOT))
    import ops.ci_tests

    env.update(ops.ci_tests.settings)
    secret = base64.b64encode(secrets.token_bytes(48)).decode()
    machine = secrets.token_urlsafe(32)
    sync_key = secrets.token_urlsafe(32)
    env.update(
        APP_PROFILE="acceptance_fixture",
        ENVIRONMENT="DEV",
        POSTGRES_HOST="127.0.0.1",
        POSTGRES_PORT=str(pgport),
        POSTGRES_USERNAME="ss_acceptance",
        POSTGRES_PASSWORD="",
        POSTGRES_DB=name + "_product",
        DB_SSL_ENABLED="False",
        DB_POOL_SIZE="2",
        DB_POOL_OVERFLOW="1",
        IS_DB_ECHO_LOG="False",
        IS_DB_FORCE_ROLLBACK="False",
        REDIS_HOST="127.0.0.1",
        REDIS_PORT=str(redisport),
        REDIS_URL="",
        REDIS_PASSWORD="",
        REDIS_SSL_ENABLED="false",
        REDIS_USERNAME="",
        SPRING_DATASOURCE_URL=f"postgresql://ss_acceptance@127.0.0.1:{pgport}/{name}_central",
        SPRING_DATASOURCE_SSL_ENABLED="false",
        SPRING_JPA_SHOW_SQL="false",
        SPRING_DATASOURCE_MAXIMUM_POOL_SIZE="3",
        EUREKA_CLIENT_ENABLED="false",
        RABBITMQ_ENABLED="False",
        RABBITMQ_HOST="127.0.0.1",
        LOKI_URL="",
        JWT_SECRET=secret,
        AUTH_SERVICE_JWT_SECRET=secret,
        JWT_SECRET_KEY=secrets.token_urlsafe(32),
        PRODUCT_IDENTITY_API_KEY=machine,
        CENTRAL_IDENTITY_CHECK_ENABLED="True",
        CENTRAL_PROFILE_API_KEY=secrets.token_urlsafe(32),
        SAMPARK_PRODUCT_UNIQUE_ID="81c53f68-35f4-4133-9e35-06f5c30354b785",
        AUTH_SERVICE_BASE_URL=f"https://127.0.0.1:{centralport}",
        SAMPARK_ENROLLMENT_SYNC_ENABLED="True",
        SAMPARK_ENROLLMENT_SYNC_KEY=sync_key,
        SAMPARK_ENROLLMENT_SYNC_URL=f"https://127.0.0.1:{centralport}/sampark/internal/samvaad-enrollment-snapshots",
        SSL_CERT_FILE=str(work / "cert.pem"),
        ACCEPTANCE_TLS_KEY=str(work / "key.pem"),
        ACCEPTANCE_TLS_CERT=str(work / "cert.pem"),
        ACCEPTANCE_CENTRAL_PORT=str(centralport),
        ACCEPTANCE_READY_FILE=str(work / "ready"),
        SAMPARK_ACCEPTANCE_CHECKOUT=str(CENTRAL),
        ACCEPTANCE_PASSWORD=secrets.token_urlsafe(24),
        PYTHONPATH=str(ROOT / "backend/backend"),
        AWS_ACCESS_KEY_ID="fixture-only-key",
        AWS_SECRET_ACCESS_KEY="fixture-only-secret",
        AWS_EC2_METADATA_DISABLED="true",
        ADMIN_EMAILS="",
        SUPER_ADMIN_EMAILS="",
        SSO_ALLOWED_ROLES="FACILITATOR,STUDENT,ADMIN",
        BACKEND_SERVER_HOST="127.0.0.1",
        BACKEND_SERVER_PORT=str(apiport),
        BACKEND_SERVER_WORKERS="1",
    )
    launch(
        [
            "redis-server",
            "--bind",
            "127.0.0.1",
            "--port",
            redisport,
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            work,
        ],
        env,
        "redis",
        work,
    )
    p = launch(
        ["node", ROOT / "ops/acceptance/central_fixture.cjs"], env, "central", work
    )
    for _ in range(150):
        if (work / "ready").exists():
            break
        if p.poll() is not None:
            raise RuntimeError("central fixture startup failed")
        time.sleep(0.2)
    else:
        raise RuntimeError("central fixture startup timeout")
    cmd(
        [PYTHON, ROOT / "ops/acceptance/product_fixture.py", "migrate"],
        env=env,
        cwd=ROOT / "backend/backend",
    )
    # Synthetic product data only; source students and enrollments are owned by the real core fixture.
    for i in [1, 2]:
        sql(
            name + "_product",
            'INSERT INTO "user"(id,email,name,password_hash,central_identity_id,feature_permissions) VALUES(%s,%s,%s,%s,%s,%s)',
            (
                i,
                f"facilitator-{chr(96+i)}@example.test",
                "Fixture Staff",
                "!fixture",
                f"fixture-facilitator-{chr(96+i)}",
                json.dumps({"course_workspace": ["read"]}),
            ),
        )
    sql(name + "_product", "INSERT INTO team(id,name) VALUES(1,'Fixture team')")
    for i in [1, 2]:
        sql(
            name + "_product",
            "INSERT INTO team_member(team_id,user_id,access_role,course_ids,colleges) VALUES(1,%s,'facilitator',%s,%s)",
            (i, json.dumps([str(i)]), json.dumps(["College A"])),
        )
    for i in [1, 2, 3]:
        sql(
            name + "_product",
            'INSERT INTO "user"(id,email,name,password_hash,sampark_student_unique_id,university) VALUES(%s,%s,%s,%s,%s,%s)',
            (
                100 + i,
                f"student-{i}@example.test",
                "Fixture Student",
                "!fixture",
                f"fixture-student-{i}",
                "College B" if i == 2 else "College A",
            ),
        )
        if i != 3:
            sql(
                name + "_product",
                "INSERT INTO practice_consent(user_id,purpose,granted,policy_version,granted_at) VALUES(%s,'facilitator_coaching',true,'fixture-v1',now())",
                (100 + i,),
            )
    # Run the actual sync implementation, which makes a real HTTP request and PostgreSQL writes.
    cmd([PYTHON, ROOT / "ops/acceptance/product_fixture.py", "sync"], env=env, cwd=work)
    api = launch(
        [PYTHON, ROOT / "ops/acceptance/product_fixture.py", "serve"], env, "api", work
    )
    origin = f"https://127.0.0.1:{apiport}"
    auth = f"https://127.0.0.1:{centralport}"
    for _ in range(150):
        try:
            if http(origin + "/api/health")[0] == 200:
                break
        except OSError:
            pass
        if api.poll() is not None:
            raise RuntimeError("product startup failed")
        time.sleep(0.2)
    else:
        raise RuntimeError("product startup timeout")
    actors = []
    tokens = []
    for i, letter in enumerate(["a", "b"], 1):
        status, response = http(
            auth + "/barabari-auth/api/auth/public/v1/login",
            body={
                "email": f"facilitator-{letter}@example.test",
                "password": env["ACCEPTANCE_PASSWORD"],
            },
        )
        assert status == 200, "real central password login failed"
        token = response["data"]["accessToken"]
        tokens.append(token)
        key = f"SAMVAAD_ACCEPTANCE_FIXTURE_{letter.upper()}_TOKEN"
        env[key] = token
        actors.append(
            {
                "label": f"facilitator-{letter}",
                "user_id": i,
                "token_env": key,
                "course_ids": [str(i)],
                "forbidden_course_ids": [str(3 - i)],
                "permissions": {"course_workspace": ["read"]},
            }
        )
    manifest = {
        "version": 1,
        "environment": "isolated_staging",
        "origin": origin,
        "actors": actors,
    }
    (work / "manifest.json").write_text(json.dumps(manifest))
    cmd(
        [
            PYTHON,
            ROOT / "ops/staff_acceptance.py",
            work / "manifest.json",
            "--report",
            work / "staff-report.json",
        ],
        env=env,
        cwd=work,
    )
    passed = ["central_password_login", "real_staff_scope_runner"]

    def roster():
        status, value = http(
            origin + "/api/v2/course-workspace/courses/1/students?limit=10", tokens[0]
        )
        assert status == 200, f"roster status {status}"
        return value

    assert [i["id"] for i in roster()["items"]] == [
        101
    ], "college or consent isolation failed"
    passed.append("college_and_consent_isolation")
    sql(
        name + "_central",
        'UPDATE "barabari-edtech".student_course_enrollments SET active=false WHERE student_id=1',
    )
    cmd([PYTHON, ROOT / "ops/acceptance/product_fixture.py", "sync"], env=env, cwd=work)
    assert roster()["total"] == 0, "source enrollment revocation not reflected"
    passed.append("source_enrollment_revocation")
    sql(
        name + "_central",
        'UPDATE "barabari-edtech".student_course_enrollments SET active=true WHERE student_id=1',
    )
    cmd([PYTHON, ROOT / "ops/acceptance/product_fixture.py", "sync"], env=env, cwd=work)
    assert roster()["total"] == 1
    passed.append("source_enrollment_restore")
    sql(
        name + "_product",
        'UPDATE "user" SET feature_permissions=%s,feature_permissions_version=feature_permissions_version+1 WHERE id=1',
        (json.dumps({}),),
    )
    assert (
        http(origin + "/api/v2/course-workspace/courses/1/students", tokens[0])[0]
        == 403
    )
    passed.append("personal_feature_revocation")
    sql(
        name + "_product",
        'UPDATE "user" SET feature_permissions=%s,feature_permissions_version=feature_permissions_version+1 WHERE id=1',
        (json.dumps({"course_workspace": ["read"]}),),
    )
    sql(
        name + "_product", "UPDATE practice_consent SET granted=false WHERE user_id=101"
    )
    assert roster()["total"] == 0
    passed.append("consent_withdrawal")
    sql(name + "_product", "UPDATE practice_consent SET granted=true WHERE user_id=101")
    assert roster()["total"] == 1
    passed.append("consent_restore")
    sql(
        name + "_product",
        "UPDATE \"user\" SET enrollment_observed_at=now()-interval '20 minutes' WHERE id=101",
    )
    assert roster()["total"] == 0
    passed.append("stale_enrollment_denial")
    cmd([PYTHON, ROOT / "ops/acceptance/product_fixture.py", "sync"], env=env, cwd=work)
    assert roster()["total"] == 1
    passed.append("fresh_enrollment_restore")
    sql(name + "_product", "UPDATE team_member SET status='inactive' WHERE user_id=1")
    assert (
        http(origin + "/api/v2/course-workspace/courses/1/students", tokens[0])[0]
        == 403
    )
    passed.append("staff_membership_revocation")
    sql(name + "_product", "UPDATE team_member SET status='active' WHERE user_id=1")
    assert roster()["total"] == 1
    passed.append("staff_membership_restore")
    sql(
        name + "_central",
        "UPDATE barabari_edtech_auth.users_identity SET is_active=false WHERE unique_id='fixture-facilitator-a'",
    )
    assert http(origin + "/api/v2/staff-access/me", tokens[0])[0] == 403
    passed.append("central_identity_revocation_same_token")
    sql(
        name + "_central",
        "UPDATE barabari_edtech_auth.users_identity SET is_active=true WHERE unique_id='fixture-facilitator-a'",
    )
    assert http(origin + "/api/v2/staff-access/me", tokens[0])[0] == 200
    passed.append("central_identity_restore_same_token")
    sql(
        name + "_central",
        "DELETE FROM barabari_edtech_auth.product_permissions WHERE user_id=(SELECT user_id FROM barabari_edtech_auth.users_identity WHERE unique_id='fixture-facilitator-a')",
    )
    assert http(origin + "/api/v2/staff-access/me", tokens[0])[0] == 403
    passed.append("central_product_revocation_same_token")
    sql(
        name + "_central",
        "INSERT INTO barabari_edtech_auth.product_permissions(user_id,product_id) SELECT u.user_id,p.product_id FROM barabari_edtech_auth.users_identity u CROSS JOIN barabari_edtech_auth.products p WHERE u.unique_id='fixture-facilitator-a' AND p.product_unique_id=%s",
        (env["SAMPARK_PRODUCT_UNIQUE_ID"],),
    )
    assert http(origin + "/api/v2/staff-access/me", tokens[0])[0] == 200
    passed.append("central_product_restore_same_token")
    source_revisions = {
        label: cmd(["git", "-C", checkout, "rev-parse", "HEAD"]).stdout.strip()
        for label, checkout in [("product", ROOT), ("sampark", CENTRAL)]
    }
    result = {
        "passed": passed,
        "postgres": sql(name + "_product", "SHOW server_version")[0][0],
        "scope": "local actual controllers/db/TLS; not hosted staging",
        "hosted_staging": False,
        "fixture_directory": str(work),
        "source_revisions": source_revisions,
        "product_worktree_dirty": bool(
            cmd(["git", "-C", ROOT, "status", "--porcelain"]).stdout.strip()
        ),
    }
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
except Exception as e:
    print("FAILED:", type(e).__name__, str(e), "fixture logs:", work, file=sys.stderr)
    raise
finally:
    for p in reversed(processes):
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
    for h in handles:
        h.close()
    if started or (work / "pg/postmaster.pid").exists():
        subprocess.run(
            [str(PG / "pg_ctl"), "-D", str(work / "pg"), "-m", "fast", "stop"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    for f in ["key.pem", "cert.pem", "manifest.json"]:
        (work / f).unlink(missing_ok=True)
