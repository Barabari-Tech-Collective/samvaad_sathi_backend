import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "staff_acceptance", ROOT / "ops/staff_acceptance.py"
)
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)
TOKEN = "sensitive-token-value-123456789"
ENV_NAME = "SAMVAAD_ACCEPTANCE_FACILITATOR_TOKEN"


def manifest():
    return {
        "version": 1,
        "environment": "isolated_staging",
        "origin": "https://isolated.example.test",
        "actors": [
            {
                "label": "facilitator-a",
                "user_id": 101,
                "token_env": ENV_NAME,
                "course_ids": ["course-a"],
                "forbidden_course_ids": ["course-b"],
                "permissions": {"course_workspace": ["read"]},
            }
        ],
    }


def fake_api(origin, path, token):
    assert origin == "https://isolated.example.test"
    assert token == TOKEN
    if path.endswith("staff-access/me"):
        return 200, {
            "user_id": 101,
            "administrator": False,
            "identity_source": "central_sso",
        }
    if path.endswith("course-workspace/me"):
        return 200, {
            "global_access": False,
            "course_ids": ["course-a"],
            "catalogue_source": "central_course_ids",
            "unassigned_access": "denied",
        }
    if path.endswith("feature-access/me"):
        return 200, {
            "administrator": False,
            "permissions": {"course_workspace": ["read"]},
            "version": 1,
        }
    if "course-a/students" in path:
        return 200, {"course_id": "course-a", "items": [], "total": 0}
    assert "course-b/students" in path
    return 403, None


def test_real_contract_matrix_and_no_evidence_payload_leaks(monkeypatch):
    calls = []

    def fetch(*args):
        calls.append(args[1])
        return fake_api(*args)

    monkeypatch.setattr(acceptance, "request", fetch)
    result = acceptance.run(manifest(), {ENV_NAME: TOKEN})
    assert result["passed"] is True
    assert len(calls) == 8
    assert "sensitive" not in json.dumps(result)
    assert "course-a" not in json.dumps(result)
    assert "permissions" not in json.dumps(result)


@pytest.mark.parametrize(
    "case",
    [
        "missing_token",
        "bad_token",
        "unknown_field",
        "production",
        "boolean_version",
        "bad_origin",
        "empty_actors",
        "boolean_user",
        "unknown_actor_field",
        "overlapping_scope",
        "empty_positive",
        "empty_negative",
        "invalid_course",
        "duplicate_course",
        "bad_operations",
        "duplicate_operations",
        "invalid_env",
        "duplicate_actor",
        "reused_identity",
        "reused_token",
    ],
)
def test_preflight_failures_never_request(monkeypatch, case):
    value, env = manifest(), {ENV_NAME: TOKEN}
    actor = value["actors"][0]
    if case == "missing_token":
        env.clear()
    elif case == "bad_token":
        env[ENV_NAME] = TOKEN + "\n"
    elif case == "unknown_field":
        value["token"] = TOKEN
    elif case == "production":
        value["environment"] = "production"
    elif case == "boolean_version":
        value["version"] = True
    elif case == "bad_origin":
        value["origin"] = "https://user:secret@example.test?token=x"
    elif case == "empty_actors":
        value["actors"] = []
    elif case == "boolean_user":
        actor["user_id"] = True
    elif case == "unknown_actor_field":
        actor["token"] = TOKEN
    elif case == "overlapping_scope":
        actor["forbidden_course_ids"] = actor["course_ids"]
    elif case == "empty_positive":
        actor["course_ids"] = []
    elif case == "empty_negative":
        actor["forbidden_course_ids"] = []
    elif case == "invalid_course":
        actor["course_ids"] = ["../course-a"]
    elif case == "duplicate_course":
        actor["course_ids"] = ["course-a", "course-a"]
    elif case == "bad_operations":
        actor["permissions"] = {"course_workspace": [True]}
    elif case == "duplicate_operations":
        actor["permissions"] = {"course_workspace": ["read", "read"]}
    elif case == "invalid_env":
        actor["token_env"] = "AWS_SECRET_ACCESS_KEY"
    else:
        other = copy.deepcopy(actor)
        if case != "duplicate_actor":
            other["label"] = "facilitator-b"
        if case == "reused_token":
            other["user_id"] = 102
        value["actors"].append(other)
    monkeypatch.setattr(
        acceptance,
        "request",
        lambda *_: pytest.fail("must validate all actors before requests"),
    )
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.run(value, env)


@pytest.mark.parametrize(
    "case",
    [
        "expired",
        "wrong_user",
        "local_token",
        "administrator",
        "global_access",
        "extra_course",
        "missing_permissions",
        "different_permissions",
        "bad_version",
        "wrong_roster",
        "denied_positive",
        "foreign_access",
        "foreign_401",
        "foreign_404",
        "foreign_500",
    ],
)
def test_invalid_baseline_or_foreign_denial_cannot_pass(monkeypatch, case):
    calls = []

    def request(origin, path, token):
        calls.append(path)
        status, body = fake_api(origin, path, token)
        if path.endswith("staff-access/me"):
            if case == "expired":
                return 401, None
            if case == "wrong_user":
                body["user_id"] = 999
            if case == "local_token":
                body["identity_source"] = "local_compatibility"
            if case == "administrator":
                body["administrator"] = True
        if path.endswith("course-workspace/me"):
            if case == "global_access":
                body["global_access"] = True
            if case == "extra_course":
                body["course_ids"].append("unexpected")
        if path.endswith("feature-access/me"):
            if case == "missing_permissions":
                del body["permissions"]
            if case == "different_permissions":
                body["permissions"] = {}
            if case == "bad_version":
                body["version"] = True
        if "course-a/students" in path:
            if case == "wrong_roster":
                body["course_id"] = "course-b"
            if case == "denied_positive":
                return 403, None
        if "course-b/students" in path:
            for name, code in [
                ("foreign_access", 200),
                ("foreign_401", 401),
                ("foreign_404", 404),
                ("foreign_500", 500),
            ]:
                if case == name:
                    return code, {"private": "must never be logged"}
        return status, body

    monkeypatch.setattr(acceptance, "request", request)
    result = acceptance.run(manifest(), {ENV_NAME: TOKEN})
    assert result["passed"] is False
    assert "private" not in json.dumps(result)
    if case == "expired":
        assert (
            len(calls) == 1
        )  # Cannot mistake failed authentication for authorization denial.


def test_all_actor_tokens_are_checked_before_first_request(monkeypatch):
    value = manifest()
    other = copy.deepcopy(value["actors"][0])
    other.update(
        label="facilitator-b", user_id=102, token_env="SAMVAAD_ACCEPTANCE_SECOND_TOKEN"
    )
    value["actors"].append(other)
    monkeypatch.setattr(
        acceptance, "request", lambda *_: pytest.fail("no partial acceptance run")
    )
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.run(value, {ENV_NAME: TOKEN})


def test_authenticated_transport_is_get_only_and_redirect_free(monkeypatch):
    from email.message import Message

    captured = []

    class Response:
        status = 200
        headers = Message()
        headers["Content-Type"] = "application/json"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, size):
            assert size == 1024 * 1024 + 1
            return b"{}"

    class Opener:
        def open(self, request, timeout):
            assert timeout == 15
            assert request.get_method() == "GET"
            assert request.get_header("Authorization") == "Bearer " + TOKEN
            return Response()

    def build(*handlers):
        captured.extend(handlers)
        return Opener()

    monkeypatch.setattr(acceptance.probe.urllib.request, "build_opener", build)
    assert acceptance.request(
        "https://isolated.example.test", "/api/v2/staff-access/me", TOKEN
    ) == (200, {})
    assert any(isinstance(h, acceptance.probe.NoRedirect) for h in captured)


@pytest.mark.parametrize("code", [401, 403, 404, 500])
def test_http_errors_are_not_serialized(monkeypatch, code):
    def fetch(*_a, **_k):
        raise acceptance.probe.SmokeError(f"http_{code}")

    monkeypatch.setattr(acceptance.probe, "fetch_json", fetch)
    assert acceptance.request(
        "https://isolated.example.test", "/api/v2/staff-access/me", TOKEN
    ) == (code, None)


def test_report_and_stdout_do_not_include_tokens_or_response_data(
    monkeypatch, tmp_path, capsys
):
    source, output = tmp_path / "manifest.json", tmp_path / "evidence.json"
    source.write_text(json.dumps(manifest()))
    monkeypatch.setenv(ENV_NAME, TOKEN)
    monkeypatch.setattr(acceptance, "request", fake_api)
    assert acceptance.main([str(source), "--report", str(output)]) == 0
    report = output.read_text()
    assert TOKEN not in report + capsys.readouterr().out
    import hashlib

    assert (
        json.loads(report)["manifest_sha256"]
        == hashlib.sha256(source.read_bytes()).hexdigest()
    )
    assert json.loads(report)["enrollment_revocation_and_journeys"] == "not_checked"


def test_cannot_overwrite_manifest(tmp_path):
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(manifest()))
    before = source.read_bytes()
    assert acceptance.main([str(source), "--report", str(source)]) == 1
    assert source.read_bytes() == before


def test_unwritable_report_fails_run(monkeypatch, tmp_path):
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps(manifest()))
    monkeypatch.setenv(ENV_NAME, TOKEN)
    monkeypatch.setattr(acceptance, "request", fake_api)
    assert acceptance.main([str(source), "--report", str(tmp_path)]) == 1


@pytest.mark.parametrize(
    "case",
    ["expired_after_negative", "permission_version_changed", "relationships_changed"],
)
def test_access_changes_after_denial_invalidate_run(monkeypatch, case):
    denied = False

    def request(origin, path, token):
        nonlocal denied
        status, body = fake_api(origin, path, token)
        if "course-b/students" in path:
            denied = True
        if denied and path.endswith("staff-access/me"):
            if case == "expired_after_negative":
                return 403, None
            if case == "relationships_changed":
                body["relationships"] = [{"role": "changed"}]
        if (
            denied
            and path.endswith("feature-access/me")
            and case == "permission_version_changed"
        ):
            body["version"] += 1
        return status, body

    monkeypatch.setattr(acceptance, "request", request)
    assert acceptance.run(manifest(), {ENV_NAME: TOKEN})["passed"] is False


def test_request_budget_is_checked_before_network(monkeypatch):
    value = manifest()
    value["actors"][0]["course_ids"] = [f"a-{i}" for i in range(100)]
    value["actors"][0]["forbidden_course_ids"] = [f"b-{i}" for i in range(100)]
    monkeypatch.setattr(
        acceptance, "request", lambda *_: pytest.fail("too many requests")
    )
    with pytest.raises(acceptance.AcceptanceError, match="request_budget"):
        acceptance.run(value, {ENV_NAME: TOKEN})


@pytest.mark.parametrize(
    "code", ["redirect_rejected", "network_or_invalid_json", "response_too_large"]
)
def test_transport_errors_fail_without_echoing_server_details(monkeypatch, code):
    monkeypatch.setattr(
        acceptance.probe,
        "fetch_json",
        lambda *_a, **_k: (_ for _ in ()).throw(acceptance.probe.SmokeError(code)),
    )
    result = acceptance.run(manifest(), {ENV_NAME: TOKEN})
    assert result["passed"] is False
    assert result["actors"][0]["error"] == "transport_or_response_failed"
