import importlib.util
from pathlib import Path

import pytest

RELEASE_PATH = Path(__file__).resolve().parents[4] / "ops" / "release.py"
EC2_RELEASE_PATH = Path(__file__).resolve().parents[4] / "ops" / "ec2-release.sh"


def load_release_module():
    spec = importlib.util.spec_from_file_location("samvaad_release", RELEASE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_release_accepts_only_the_current_default_branch_tip(monkeypatch):
    release = load_release_module()
    sha = "a" * 40
    monkeypatch.setenv("GITHUB_REF_NAME", "master")

    def command(*args):
        if args[-1] == "repos/example/samvaad":
            return {"default_branch": "master"}
        if args[-1] == "repos/example/samvaad/commits/master":
            return {"sha": sha}
        raise AssertionError(f"Unexpected command: {args}")

    monkeypatch.setattr(release, "command", command)
    release.verify_production_source("example/samvaad", sha)


def test_production_release_rejects_a_feature_branch(monkeypatch):
    release = load_release_module()
    monkeypatch.setenv("GITHUB_REF_NAME", "feature-branch")
    monkeypatch.setattr(
        release,
        "command",
        lambda *args: {"default_branch": "master"},
    )

    with pytest.raises(RuntimeError, match="default branch"):
        release.verify_production_source("example/samvaad", "a" * 40)


def test_production_release_rejects_a_stale_default_branch_commit(monkeypatch):
    release = load_release_module()
    monkeypatch.setenv("GITHUB_REF_NAME", "master")

    responses = iter(
        [
            {"default_branch": "master"},
            {"sha": "b" * 40},
        ]
    )
    monkeypatch.setattr(release, "command", lambda *args: next(responses))

    with pytest.raises(RuntimeError, match="default-branch tip"):
        release.verify_production_source("example/samvaad", "a" * 40)


def test_backend_release_requires_real_database_backup_evidence(monkeypatch):
    release = load_release_module()
    monkeypatch.setenv("DATABASE_BACKUP_REFERENCE", "not applicable")

    with pytest.raises(RuntimeError, match="backup ID"):
        release.verify_database_backup_reference()


def test_backend_release_accepts_a_timestamped_backup_reference(monkeypatch):
    release = load_release_module()
    monkeypatch.setenv(
        "DATABASE_BACKUP_REFERENCE", "aiven-backup-2026-10-14T14:30:00+05:30"
    )

    assert release.verify_database_backup_reference().startswith("aiven-backup-")


def test_hosted_smoke_check_identifies_itself_to_edge_security(monkeypatch):
    release = load_release_module()
    monkeypatch.setenv("HEALTH_URL", "https://backend.example.test/api/health")
    captured = {}

    from email.message import Message

    class HealthyResponse:
        status = 200
        headers = Message()
        headers["Content-Type"] = "application/json"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size):
            assert size == 64 * 1024 + 1
            return b'{"status":"healthy","service":"samvaad-sathi-backend"}'

    class Opener:
        def open(self, request, *, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return HealthyResponse()

    monkeypatch.setattr(
        release.public_smoke.urllib.request, "build_opener", lambda *_: Opener()
    )

    release.smoke()

    assert captured["request"].full_url.endswith("/api/health")
    assert captured["request"].get_header("User-agent") == (
        "Samvaad-Release-Health-Check/1.0"
    )
    assert captured["request"].get_header("Accept") == "application/json"
    assert captured["timeout"] == 30


def test_ec2_release_verifies_migrations_before_restarting_services():
    script = EC2_RELEASE_PATH.read_text()

    migration = script.index('"$VENV/bin/python" -m alembic upgrade head')
    revision_check = script.index("Database revision mismatch")
    drift_check = script.index('"$VENV/bin/python" ops/check_schema_drift.py')
    restart = script.index(
        'sudo systemctl restart "$API_SERVICE" "$WORKER_SERVICE"',
        drift_check,
    )

    assert migration < revision_check < drift_check < restart


def test_ec2_release_has_a_local_health_gate_and_application_rollback():
    script = EC2_RELEASE_PATH.read_text()

    assert "http://127.0.0.1:8000/api/health" in script
    rollback_checkout = script.index('git checkout --quiet --force "$previous_sha"')
    assert script.rfind('cd "$APP_ROOT"', 0, rollback_checkout) != -1
    assert "Database downgrade is intentionally NOT automatic" in script


@pytest.mark.parametrize(
    "expected,actual", [("222222222222", "111111111111"), ("invalid", "222222222222")]
)
def test_release_rejects_wrong_or_malformed_destination(monkeypatch, expected, actual):
    release = load_release_module()
    monkeypatch.setenv("AWS_EXPECTED_ACCOUNT_ID", expected)
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setattr(release, "command", lambda *args: {"Account": actual})
    with pytest.raises(RuntimeError, match="account mismatch|12 digits"):
        release.verify_aws_destination()


def test_release_rejects_unconfigured_destination_before_aws_call(monkeypatch):
    release = load_release_module()
    monkeypatch.delenv("AWS_EXPECTED_ACCOUNT_ID", raising=False)

    def no_call(*args):
        raise AssertionError("No AWS call should occur before configuration validation")

    monkeypatch.setattr(release, "command", no_call)
    with pytest.raises(RuntimeError, match="AWS_EXPECTED_ACCOUNT_ID"):
        release.verify_aws_destination()


def test_release_accepts_only_the_explicit_destination(monkeypatch):
    release = load_release_module()
    monkeypatch.setenv("AWS_EXPECTED_ACCOUNT_ID", "222222222222")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    calls = []

    def command(*args):
        calls.append(args)
        return {"Account": "222222222222"}

    monkeypatch.setattr(release, "command", command)
    assert release.verify_aws_destination() == "222222222222"
    assert calls == [
        (
            "aws",
            "sts",
            "get-caller-identity",
            "--region",
            "eu-west-1",
            "--output",
            "json",
        )
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://backend.test/api/health",
        "https://user:secret@backend.test/api/health",
        "https://backend.test/api/health?token=secret",
        "https://backend.test/api/health#x",
        "https://backend.test/api/health?",
        "https://backend.test/api/health/",
        "https://backend.test/api/../health",
        "https://backend.test//health",
        "https://backend.test:0/api/health",
        "https://backend.test:bad/api/health",
        "https://backend.test/api/health\n",
        "https://backend.test/api\\other/health",
    ],
)
def test_hosted_smoke_rejects_bad_target_before_network_or_retry(monkeypatch, url):
    release = load_release_module()
    monkeypatch.setenv("HEALTH_URL", url)
    monkeypatch.setattr(
        release.public_smoke,
        "fetch_json",
        lambda *_a, **_k: pytest.fail("network must not run"),
    )
    monkeypatch.setattr(
        release.time,
        "sleep",
        lambda *_: pytest.fail("invalid configuration must not retry"),
    )
    with pytest.raises(RuntimeError, match="invalid_https_health_url") as error:
        release.smoke()
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://backend.test/health",
        "https://backend.test/v2/api/health",
        "https://backend.test:8443/api/health",
    ],
)
def test_hosted_health_accepts_explicit_deployment_prefix(monkeypatch, url):
    release = load_release_module()
    monkeypatch.setenv("HEALTH_URL", url)
    calls = []

    def fetch(origin, path, **kwargs):
        calls.append(origin + path)
        return {"status": "healthy", "service": "samvaad-sathi-backend"}

    monkeypatch.setattr(release.public_smoke, "fetch_json", fetch)
    release.smoke()
    assert calls == [url]  # No OpenAPI or authenticated endpoint requirement.


@pytest.mark.parametrize(
    "result",
    [
        {},
        [],
        {"status": "unhealthy", "service": "samvaad-sathi-backend"},
        {"status": "healthy", "service": "other-private-service"},
    ],
)
def test_hosted_health_contract_failure_exhausts_bounded_retries(monkeypatch, result):
    release = load_release_module()
    monkeypatch.setenv("HEALTH_URL", "https://backend.test/api/health")
    calls, sleeps = [], []

    def fetch(*_args, **kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(release.public_smoke, "fetch_json", fetch)
    monkeypatch.setattr(release.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="health_contract_failed") as error:
        release.smoke()
    assert len(calls) == 6
    assert sleeps == [10] * 5
    assert "private" not in str(error.value)


@pytest.mark.parametrize(
    "initial",
    [
        "network_or_invalid_json",
        "http_503",
        "redirect_rejected",
        "response_too_large",
        "unexpected_content_type",
    ],
)
def test_hosted_health_recovers_after_transient_failure(monkeypatch, initial):
    release = load_release_module()
    monkeypatch.setenv("HEALTH_URL", "https://backend.test/api/health")
    calls, sleeps = [], []

    def fetch(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise release.public_smoke.SmokeError(initial)
        return {"status": "healthy", "service": "samvaad-sathi-backend"}

    monkeypatch.setattr(release.public_smoke, "fetch_json", fetch)
    monkeypatch.setattr(release.time, "sleep", sleeps.append)
    release.smoke()
    assert len(calls) == 2
    assert sleeps == [10]


def configure_main(monkeypatch, release):
    import json

    for name, value in {
        "GITHUB_SHA": "a" * 40,
        "GITHUB_REPOSITORY": "example/backend",
        "TEST_EVIDENCE": "tester, date, acceptance results and approval link",
        "PRODUCTION_PROVIDER": "ec2",
        "DATABASE_BACKUP_REFERENCE": "verified-backup-2026-10-20",
        "HEALTH_URL": "https://backend.test/api/health",
    }.items():
        monkeypatch.setenv(name, value)
    events = []
    monkeypatch.setattr(
        release, "verify_aws_destination", lambda: events.append("aws-validated")
    )
    monkeypatch.setattr(
        release,
        "verify_production_source",
        lambda *_: events.append("source-validated"),
    )
    monkeypatch.setattr(
        release.subprocess,
        "check_output",
        lambda *_a, **_k: (
            events.append("deployment-created") or json.dumps({"id": 123})
        ),
    )

    def command(*args):
        events.append(
            next(arg for arg in args if arg.startswith("state=")).split("=", 1)[1]
        )
        return {}

    monkeypatch.setattr(release, "command", command)
    monkeypatch.setattr(release, "ec2_deploy", lambda *_: events.append("deployed"))
    monkeypatch.setattr(release.time, "sleep", lambda *_: None)
    return events


def test_main_rejects_health_configuration_before_aws_or_deployment(monkeypatch):
    release = load_release_module()
    events = configure_main(monkeypatch, release)
    monkeypatch.setenv("HEALTH_URL", "https://user:secret@backend.test/api/health")
    with pytest.raises(RuntimeError, match="invalid_https_health_url"):
        release.main()
    assert events == []


@pytest.mark.parametrize("healthy", [True, False])
def test_main_only_marks_success_after_verified_health(monkeypatch, healthy):
    release = load_release_module()
    events = configure_main(monkeypatch, release)

    def fetch(*_a, **_k):
        events.append("health-request")
        return (
            {"status": "healthy", "service": "samvaad-sathi-backend"}
            if healthy
            else {"status": "healthy", "service": "unrelated"}
        )

    monkeypatch.setattr(release.public_smoke, "fetch_json", fetch)
    if healthy:
        release.main()
        assert events[-2:] == ["health-request", "success"]
        assert events.count("health-request") == 1
    else:
        with pytest.raises(RuntimeError, match="Hosted smoke check failed"):
            release.main()
        assert events[-1] == "failure"
        assert "success" not in events
        assert events.count("health-request") == 6
    assert events.index("deployed") < events.index("health-request")


@pytest.mark.parametrize(
    "case",
    [
        "html",
        "invalid_json",
        "too_large",
        "non_200",
        "timeout",
        "truncated",
        "redirect",
    ],
)
def test_hosted_transport_failures_never_pass_release(monkeypatch, case):
    from email.message import Message
    import http.client

    release = load_release_module()
    monkeypatch.setenv("HEALTH_URL", "https://backend.test/api/health")
    requests, sleeps = [], []

    class Response:
        status = 503 if case == "non_200" else 200
        headers = Message()
        headers["Content-Type"] = "text/html" if case == "html" else "application/json"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, size):
            assert size == 64 * 1024 + 1
            if case == "truncated":
                raise http.client.IncompleteRead(b"private body", 30)
            return b"x" * size if case == "too_large" else b"private invalid JSON"

    class Opener:
        def __init__(self, handler):
            self.handler = handler

        def open(self, request, *, timeout):
            requests.append(request)
            if case == "timeout":
                raise TimeoutError("private network diagnostic")
            if case == "redirect":
                return self.handler.redirect_request(
                    request,
                    None,
                    302,
                    "private diagnostic",
                    {},
                    "https://other.test/private",
                )
            return Response()

    monkeypatch.setattr(release.public_smoke.urllib.request, "build_opener", Opener)
    monkeypatch.setattr(release.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="Hosted smoke check failed") as error:
        release.smoke()
    assert len(requests) == 6
    assert sleeps == [10] * 5
    assert "private" not in str(error.value)


def test_hosted_fetch_failure_on_final_attempt_does_not_sleep_again(monkeypatch):
    release = load_release_module()
    monkeypatch.setenv("HEALTH_URL", "https://backend.test/api/health")
    calls, sleeps = [], []

    def fetch(*_a, **_k):
        calls.append(True)
        raise release.public_smoke.SmokeError("http_503")

    monkeypatch.setattr(release.public_smoke, "fetch_json", fetch)
    monkeypatch.setattr(release.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="http_503"):
        release.smoke()
    assert len(calls) == 6
    assert len(sleeps) == 5
