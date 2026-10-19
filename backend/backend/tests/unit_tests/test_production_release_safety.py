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

    class HealthyResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return HealthyResponse()

    monkeypatch.setattr(release.urllib.request, "urlopen", urlopen)

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


@pytest.mark.parametrize("expected,actual", [("222222222222", "111111111111"), ("invalid", "222222222222")])
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
    assert calls == [("aws", "sts", "get-caller-identity", "--region", "eu-west-1", "--output", "json")]
