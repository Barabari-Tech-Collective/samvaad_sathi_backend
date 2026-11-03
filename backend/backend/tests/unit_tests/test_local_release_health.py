import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "local_probe", ROOT / "ops/local_health.py"
)
local = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(local)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8000/api/health",
        "http://localhost:8000/api/health",
        "http://127.0.0.1.evil:8000/api/health",
        "http://127.0.0.1:8000@evil/api/health",
        "http://user:secret@127.0.0.1:8000/api/health",
        "http://127.0.0.1/api/health",
        "http://127.0.0.1:0/api/health",
        "http://127.0.0.1:65536/api/health",
        "http://127.0.0.1:8000/api/health?secret=x",
        "http://127.0.0.1:8000/api/health#x",
        "http://127.0.0.1:8000/api/health?",
        "http://127.0.0.1:8000/api/../health",
        "http://127.0.0.1:8000//health",
        "http://127.0.0.1:8000/api/health\n",
    ],
)
def test_invalid_local_target_cannot_issue_a_request(monkeypatch, capsys, url):
    monkeypatch.setattr(
        local.probe, "fetch_json", lambda *_a, **_k: pytest.fail("network prohibited")
    )
    assert local.main([url]) == 1
    assert "secret" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/api/health",
        "http://127.0.0.1:9000/health",
        "http://127.0.0.1:8000/v2/api/health",
    ],
)
def test_validate_only_never_requests_health(monkeypatch, url):
    monkeypatch.setattr(
        local.probe, "fetch_json", lambda *_a, **_k: pytest.fail("network prohibited")
    )
    assert local.main([url, "--validate-only"]) == 0


def test_local_probe_disables_environment_proxies_and_bounds_response(monkeypatch):
    from email.message import Message

    handlers_seen = []

    class Response:
        status = 200
        headers = Message()
        headers["Content-Type"] = "application/json"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, size):
            assert size == 64 * 1024 + 1
            return b'{"status":"healthy","service":"samvaad-sathi-backend"}'

    class Opener:
        def open(self, request, timeout):
            assert timeout == 5
            assert (
                request.get_header("User-agent")
                == "Samvaad-Local-Release-Health-Check/1.0"
            )
            return Response()

    def build(*handlers):
        handlers_seen.extend(handlers)
        return Opener()

    monkeypatch.setenv("HTTP_PROXY", "http://untrusted.invalid")
    monkeypatch.setattr(local.probe.urllib.request, "build_opener", build)
    assert local.main(["http://127.0.0.1:8000/api/health"]) == 0
    assert any(isinstance(h, local.probe.NoRedirect) for h in handlers_seen)
    assert any(
        isinstance(h, local.probe.urllib.request.ProxyHandler) and h.proxies == {}
        for h in handlers_seen
    )


STUB = r"""
import importlib.util
import json
import os
from pathlib import Path
import sys
name = Path(sys.argv[0]).name
args = sys.argv[1:]
base = Path(os.environ['FIXTURE_ROOT'])
scenario = os.environ['SCENARIO']
state_path = base / 'state'
state = state_path.read_text() if state_path.exists() else 'previous'
with (base / 'events').open('a') as log:
    log.write(json.dumps([name, args, state]) + '\n')
if name == 'id': print('ubuntu')
elif name == 'git':
    if args[0] == 'rev-parse': print('a' * 40)
    elif args[0] == 'show':
        print((Path(os.environ['SOURCE_REPO']) / args[-1].split(':', 1)[1]).read_text())
    elif args[0] == 'checkout':
        old = args[-1] == 'a' * 40
        if old and scenario == 'rollback_checkout': sys.exit(1)
        state_path.write_text('previous' if old else 'candidate')
        if old:
            for filename in ['local_health.py', 'staging_smoke.py']:
                (base / 'app/ops' / filename).unlink(missing_ok=True)
elif name == 'systemctl':
    if args[0] == 'is-active' and 'worker' in args[-1]:
        if scenario == 'worker_both' or (state == 'candidate' and scenario == 'worker_candidate'):
            sys.exit(1)
elif name == 'sudo':
    if state == 'previous' and scenario == 'rollback_restart': sys.exit(1)
elif name == 'pip':
    if state == 'previous' and scenario == 'rollback_dependencies': sys.exit(1)
    if state == 'candidate' and scenario == 'candidate_dependencies': sys.exit(1)
elif name == 'python':
    if args[0] == '-m':
        if args[-1] == 'heads' or args[-1] == 'current': print('abc123 (head)')
        if args[-1] == 'heads' and scenario == 'heads_failure': sys.exit(1)
        if args[-1] == 'head' and scenario == 'migration_failure': sys.exit(1)
    elif args[0].endswith('local_health.py'):
        spec = importlib.util.spec_from_file_location('snapshot_probe', args[0])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        def fetch(*_a, **_k):
            if state == 'candidate' and scenario != 'success':
                return {'status': 'healthy', 'service': 'wrong-service'}
            return {'status': 'healthy', 'service': 'samvaad-sathi-backend'}
        mod.probe.fetch_json = fetch
        sys.exit(mod.main(args[1:]))
"""


def run_release(tmp_path, scenario, health_url=None):
    app = tmp_path / "app"
    (app / ".git").mkdir(parents=True)
    (app / "backend/backend").mkdir(parents=True)
    (app / "backend/.env").touch()
    (app / "backend/backend/requirements.txt").touch()
    (app / "ops").mkdir()
    for name in ["local_health.py", "staging_smoke.py"]:
        shutil.copy(ROOT / "ops" / name, app / "ops" / name)
    bins = tmp_path / "bin"
    bins.mkdir()
    for name in ["git", "id", "flock", "systemctl", "sudo", "sleep", "python", "pip"]:
        p = bins / name
        p.write_text(f"#!{sys.executable}\n" + STUB)
        p.chmod(0o700)
    # macOS ships Bash 3; this supplies only the missing mapfile builtin used by
    # the unmodified migration flow. Linux CI runs the actual Bash builtin.
    env_file = tmp_path / "bash-env"
    env_file.write_text(
        'if [[ ${BASH_VERSINFO[0]} -lt 4 ]]; then\nmapfile() { migration_heads=(); while IFS= read -r row; do migration_heads+=("$row"); done; }\nfi\n'
    )
    script = tmp_path / "release.sh"
    script.write_text(
        (ROOT / "ops/ec2-release.sh")
        .read_text()
        .replace("/tmp/samvaad-release.lock", str(tmp_path / "release.lock"))
    )
    env = {
        **os.environ,
        "FIXTURE_ROOT": str(tmp_path),
        "SCENARIO": scenario,
        "SOURCE_REPO": str(ROOT),
        "PATH": str(bins) + os.pathsep + os.environ["PATH"],
        "APP_ROOT": str(app),
        "VENV": str(tmp_path),
        "BASH_ENV": str(env_file),
    }
    if health_url is not None:
        env["HEALTH_URL"] = health_url
    else:
        env.pop("HEALTH_URL", None)
    result = subprocess.run(
        ["bash", str(script), "b" * 40],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    events = [
        json.loads(line) for line in (tmp_path / "events").read_text().splitlines()
    ]
    return result, events, app


def test_shell_success_requires_both_services_and_contract(tmp_path):
    result, events, _ = run_release(tmp_path, "success")
    assert result.returncode == 0, result.stderr
    assert "Released " in result.stdout
    checks = [args for name, args, _ in events if name == "systemctl"]
    assert checks == [
        ["is-active", "--quiet", "samvaad_sathi_backend.service"],
        ["is-active", "--quiet", "samvaad-worker.service"],
    ]
    assert not any(name == "git" and args[-1] == "a" * 40 for name, args, _ in events)


@pytest.mark.parametrize("scenario", ["bad_contract", "worker_candidate"])
def test_shell_failed_candidate_restores_old_commit_with_snapshot_probe(
    tmp_path, scenario
):
    result, events, app = run_release(tmp_path, scenario)
    assert result.returncode != 0
    assert "Rolled back and healthy" in result.stdout, result.stdout + result.stderr
    assert "Released " not in result.stdout
    assert not (app / "ops/local_health.py").exists()
    assert "Database downgrade is intentionally NOT automatic" in result.stdout
    assert not any("downgrade" in args for _, args, _ in events)
    probes = [
        args[0]
        for name, args, _ in events
        if name == "python" and args[0].endswith("local_health.py")
    ]
    assert len(set(probes)) == 1
    assert not Path(probes[0]).parent.exists()  # EXIT trap cleaned the snapshot.


@pytest.mark.parametrize(
    "scenario",
    ["rollback_checkout", "rollback_dependencies", "rollback_restart", "worker_both"],
)
def test_shell_cannot_claim_healthy_after_incomplete_rollback(tmp_path, scenario):
    result, events, _ = run_release(tmp_path, scenario)
    assert result.returncode != 0
    assert "URGENT:" in result.stdout
    assert "Rolled back and healthy" not in result.stdout
    assert "Released " not in result.stdout
    old_installs = [
        1 for name, _, state in events if name == "pip" and state == "previous"
    ]
    assert len(old_installs) == (0 if scenario == "rollback_checkout" else 1)


def test_shell_invalid_target_fails_before_checkout_or_restart(tmp_path):
    result, events, _ = run_release(
        tmp_path, "success", "http://127.0.0.1:8000@evil/api/health"
    )
    assert result.returncode != 0
    assert not any(
        name == "sudo" or (name == "git" and args[0] == "checkout")
        for name, args, _ in events
    )


@pytest.mark.parametrize(
    "scenario", ["candidate_dependencies", "heads_failure", "migration_failure"]
)
def test_shell_early_failure_restores_exactly_once(tmp_path, scenario):
    result, events, _ = run_release(tmp_path, scenario)
    assert result.returncode != 0
    assert "Rolled back and healthy" in result.stdout, result.stdout + result.stderr
    old_checkouts = [
        1
        for name, args, _ in events
        if name == "git" and args[0] == "checkout" and args[-1] == "a" * 40
    ]
    assert len(old_checkouts) == 1
    assert "Released " not in result.stdout
    if scenario != "migration_failure":
        assert not any(
            name == "python" and args == ["-m", "alembic", "upgrade", "head"]
            for name, args, _ in events
        )
