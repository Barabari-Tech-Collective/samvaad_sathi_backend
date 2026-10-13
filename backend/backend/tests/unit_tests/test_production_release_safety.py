import importlib.util
from pathlib import Path

import pytest


RELEASE_PATH = Path(__file__).resolve().parents[4] / "ops" / "release.py"


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
