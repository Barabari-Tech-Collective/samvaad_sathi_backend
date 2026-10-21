import importlib.util
import json
from email.message import Message
from pathlib import Path
import urllib.error

import pytest

SPEC = importlib.util.spec_from_file_location(
    "public_smoke", Path(__file__).resolve().parents[4] / "ops/staging_smoke.py"
)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com/api",
        "https://example.com?token=secret",
        "https://example.com#secret",
        "https://example.com?",
        "https://example.com:0",
        "https://example.com:bad",
        "https://",
        "https://example.com\\evil",
        "https://example.com\n",
    ],
)
def test_invalid_target_never_issues_request(monkeypatch, url):
    monkeypatch.setattr(
        smoke, "fetch_json", lambda *_: pytest.fail("must not send request")
    )
    with pytest.raises(smoke.SmokeError, match="invalid_https_origin"):
        smoke.run(url, "/api")


@pytest.mark.parametrize(
    "prefix", ["api", "/api/", "//evil", "/../api", "/api?q=secret"]
)
def test_rejects_ambiguous_prefix(prefix):
    with pytest.raises(smoke.SmokeError, match="invalid_api_prefix"):
        smoke.validate_prefix(prefix)


def contracts(prefix="/api"):
    return {
        "openapi": "3.1.0",
        "paths": {
            prefix + path: {"get": {"responses": {"200": {"description": "OK"}}}}
            for path in [
                "/health",
                "/audio/attempts/{question_attempt_id}/transcription",
            ]
        },
    }


@pytest.mark.parametrize("prefix", ["/api", "", "/v2/api"])
def test_real_contract_paths_and_root_docs(monkeypatch, prefix):
    calls = []

    def fetch(origin, path):
        calls.append((origin, path))
        return (
            {"status": "healthy", "service": "samvaad-sathi-backend"}
            if path.endswith("/health")
            else contracts(prefix)
        )

    monkeypatch.setattr(smoke, "fetch_json", fetch)
    assert smoke.run("https://example.com/", prefix)["api_path_count"] == 2
    assert calls == [
        ("https://example.com", prefix + "/health"),
        ("https://example.com", "/openapi.json"),
    ]


@pytest.mark.parametrize(
    "health",
    [{}, {"status": "unhealthy"}, {"status": "healthy", "service": "other"}, []],
)
def test_health_is_not_just_any_json_object(monkeypatch, health):
    monkeypatch.setattr(smoke, "fetch_json", lambda *_: health)
    with pytest.raises(smoke.SmokeError, match="health_contract_failed"):
        smoke.run("https://example.com", "/api")


@pytest.mark.parametrize(
    "schema",
    [
        {},
        {"openapi": "3.1.0", "paths": []},
        {
            "openapi": "3.1.0",
            "paths": {"/api/health": {"post": {"responses": {"200": {}}}}},
        },
    ],
)
def test_missing_or_malformed_operations_fail(monkeypatch, schema):
    monkeypatch.setattr(
        smoke,
        "fetch_json",
        lambda _, path: (
            {"status": "healthy", "service": "samvaad-sathi-backend"}
            if path.endswith("/health")
            else schema
        ),
    )
    with pytest.raises(smoke.SmokeError):
        smoke.run("https://example.com", "/api")


class Response:
    status = 200

    def __init__(self, payload=b"{}", content_type="application/json"):
        self.payload = payload
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size):
        assert size == smoke.MAX_RESPONSE_BYTES + 1
        return self.payload[:size]


@pytest.mark.parametrize(
    "response,error",
    [
        (Response(b"broken"), "network_or_invalid_json"),
        (Response(content_type="text/html"), "unexpected_content_type"),
        (Response(b"x" * (smoke.MAX_RESPONSE_BYTES + 1)), "response_too_large"),
    ],
)
def test_bounded_validated_network_response(monkeypatch, response, error):
    class Opener:
        def open(self, request, timeout):
            assert request.get_header("Accept") == "application/json"
            assert timeout == 15
            return response

    monkeypatch.setattr(smoke.urllib.request, "build_opener", lambda *_: Opener())
    with pytest.raises(smoke.SmokeError, match=error):
        smoke.fetch_json("https://example.com", "/health")


def test_redirect_is_rejected_before_following():
    with pytest.raises(smoke.SmokeError, match="redirect_rejected"):
        smoke.NoRedirect().redirect_request(
            None, None, 302, "redirect", {}, "https://other.test/secret"
        )


def test_failure_evidence_contains_no_input_secrets(tmp_path, capsys):
    report = tmp_path / "result.json"
    assert smoke.main(["https://user:secret@example.com", "--report", str(report)]) == 1
    data = json.loads(report.read_text())
    assert data["passed"] is False
    assert data["authenticated_acceptance"] == "not_checked"
    assert "secret" not in report.read_text() + capsys.readouterr().err


def test_report_write_failure_cannot_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(smoke, "run", lambda *_: {"api_path_count": 2})
    assert smoke.main(["https://example.com", "--report", str(tmp_path)]) == 1


@pytest.mark.parametrize("code", [301, 401, 403, 404, 500])
def test_http_failure_is_redacted(monkeypatch, code):
    class Opener:
        def open(self, *_args, **_kwargs):
            raise urllib.error.HTTPError(
                "https://secret.test/token", code, "private message", {}, None
            )

    monkeypatch.setattr(smoke.urllib.request, "build_opener", lambda *_: Opener())
    with pytest.raises(smoke.SmokeError, match=f"^http_{code}$"):
        smoke.fetch_json("https://example.com", "/health")


def test_successful_evidence_is_explicitly_public_only(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        smoke,
        "fetch_json",
        lambda _, path: (
            {"status": "healthy", "service": "samvaad-sathi-backend"}
            if path.endswith("/health")
            else contracts()
        ),
    )
    report = tmp_path / "result.json"
    assert smoke.main(["https://example.com/", "--report", str(report)]) == 0
    data = json.loads(report.read_text())
    assert data["passed"] is True
    assert data["target_origin"] == "https://example.com"
    assert data["authenticated_acceptance"] == "not_checked"
    assert json.loads(capsys.readouterr().out) == data


@pytest.mark.parametrize(
    "operation",
    [
        None,
        {"post": {"responses": {"200": {}}}},
        {"get": {"responses": {"202": {}}}},
        {"get": {"responses": {"200": []}}},
    ],
)
def test_transcription_must_have_a_get_200_contract(monkeypatch, operation):
    schema = contracts()
    path = "/api/audio/attempts/{question_attempt_id}/transcription"
    schema["paths"][path] = operation
    monkeypatch.setattr(
        smoke,
        "fetch_json",
        lambda _, path: (
            {"status": "healthy", "service": "samvaad-sathi-backend"}
            if path.endswith("/health")
            else schema
        ),
    )
    with pytest.raises(smoke.SmokeError, match="required_get_contract_missing"):
        smoke.run("https://example.com", "/api")
