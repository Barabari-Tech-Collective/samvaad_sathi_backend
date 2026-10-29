from unittest.mock import MagicMock

import pytest

from src.services.aws_storage_config import load_storage_configuration


def reader(**values):
    return lambda key, default="": values.get(key, default)


def test_missing_bucket_never_defaults_to_the_previous_account():
    with pytest.raises(ValueError, match="explicitly select"):
        load_storage_configuration(reader())


def test_local_storage_is_explicit_and_uses_matching_loopback_delivery():
    value = load_storage_configuration(reader(
        ENVIRONMENT="DEV", AWS_S3_BUCKET_NAME="local-files",
        AWS_ACCESS_KEY_ID="local", AWS_SECRET_ACCESS_KEY="local-only",
        AWS_S3_LOCAL_ENDPOINT_URL="http://127.0.0.1:19000",
        AWS_S3_PUBLIC_BASE_URL="http://127.0.0.1:19000/local-files",
    ))
    assert value.client_options["endpoint_url"] == "http://127.0.0.1:19000"
    assert value.public_base_url == "http://127.0.0.1:19000/local-files"


@pytest.mark.parametrize("environment,endpoint", [
    ("PROD", "http://127.0.0.1:19000"), ("STAGE", "http://127.0.0.1:19000"),
    ("DEV", "http://external.test:19000"), ("DEV", "http://127.0.0.1:19000?token=x"),
])
def test_local_storage_cannot_redirect_deployed_storage(environment, endpoint):
    with pytest.raises(ValueError, match="Local S3"):
        load_storage_configuration(reader(ENVIRONMENT=environment,
            AWS_S3_BUCKET_NAME="local-files", AWS_ACCESS_KEY_ID="local",
            AWS_SECRET_ACCESS_KEY="local-only", AWS_S3_LOCAL_ENDPOINT_URL=endpoint))


def test_no_static_keys_uses_refreshable_default_credentials():
    value = load_storage_configuration(reader(AWS_S3_BUCKET_NAME="new-private-bucket", AWS_REGION="eu-west-1"))
    assert value.client_options == {"region_name": "eu-west-1"}
    assert value.bucket == "new-private-bucket"


def test_temporary_credentials_keep_the_session_token_without_logging_secrets():
    value = load_storage_configuration(reader(
        AWS_S3_BUCKET_NAME="new-private-bucket", AWS_ACCESS_KEY_ID="temporary-access",
        AWS_SECRET_ACCESS_KEY="private-secret", AWS_SESSION_TOKEN="private-token",
    ))
    assert value.client_options["aws_session_token"] == "private-token"
    assert "private-secret" not in repr(value)
    assert "private-token" not in repr(value)


@pytest.mark.parametrize("credentials", [
    {"AWS_ACCESS_KEY_ID": "access"}, {"AWS_SECRET_ACCESS_KEY": "secret"},
    {"AWS_SESSION_TOKEN": "token"},
])
def test_partial_explicit_credentials_fail_closed(credentials):
    with pytest.raises(ValueError, match="complete"):
        load_storage_configuration(reader(AWS_S3_BUCKET_NAME="new-private-bucket", **credentials))


@pytest.mark.parametrize("origin", ["http://media.example.test", "https://user:password@media.example.test", "https://media.example.test?token=secret"])
def test_public_tts_origin_rejects_insecure_or_credential_urls(origin):
    with pytest.raises(ValueError, match="HTTPS"):
        load_storage_configuration(reader(AWS_S3_BUCKET_NAME="new-private-bucket", AWS_S3_PUBLIC_BASE_URL=origin))


def test_tts_prediction_and_upload_use_the_same_new_origin(monkeypatch):
    from src.services import s3_service
    from src.api.routes.interviews_v2 import _prepare_audio_for_questions, settings

    storage = load_storage_configuration(reader(
        AWS_S3_BUCKET_NAME="new-private-bucket", AWS_REGION="eu-west-1",
        AWS_S3_PUBLIC_BASE_URL="https://media.example.test/questions/",
    ))
    client = MagicMock()
    monkeypatch.setattr(s3_service, "STORAGE_CONFIGURATION", storage)
    monkeypatch.setattr(s3_service, "BUCKET_NAME", storage.bucket)
    monkeypatch.setattr(s3_service, "s3_client", client)
    monkeypatch.setattr(settings, "MOCK_TTS", False)
    questions = [{"text": "Explain your project"}]
    pending = _prepare_audio_for_questions(questions)
    uploaded = s3_service.upload_audio_to_s3(b"audio", pending[0]["h_id"])
    assert uploaded == questions[0]["audio_url"]
    assert uploaded.startswith("https://media.example.test/questions/Samvaad-Saathi/tts-audio/")
    assert client.put_object.call_args.kwargs["Bucket"] == "new-private-bucket"
    assert _prepare_audio_for_questions(questions) == []
