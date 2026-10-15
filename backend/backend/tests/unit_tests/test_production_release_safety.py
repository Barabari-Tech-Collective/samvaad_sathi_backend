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
    assert 'git checkout --quiet --force "$previous_sha"' in script
    assert "Database downgrade is intentionally NOT automatic" in script
