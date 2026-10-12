"""Answer recordings must survive the request that uploaded them.

save_audio_file only ever produced a temporary file, deleted on the way out of
the request, and the audio_url column held a generated name pointing at
nothing. A failed transcription therefore lost the recording permanently, an
answer could not be re-transcribed after a provider outage, and a disputed
score could not be checked against what the student actually said. It is also
the prerequisite for moving transcription off the request path: a background
worker needs something to fetch.
"""

from unittest import mock

import pytest

from src.services import s3_service


@pytest.fixture
def s3(monkeypatch):
    client = mock.MagicMock()
    monkeypatch.setattr(s3_service, "s3_client", client)
    monkeypatch.setattr(s3_service, "BUCKET_NAME", "test-bucket")
    return client


@pytest.mark.anyio
async def test_recording_is_stored_encrypted_and_privately(s3):
    key = await s3_service.upload_answer_audio_to_s3(
        audio_bytes=b"RIFFfake-wav-bytes", user_id=7, question_attempt_id=42
    )

    assert key is not None
    s3.put_object.assert_called_once()
    call = s3.put_object.call_args.kwargs
    assert call["Bucket"] == "test-bucket"
    assert call["Body"] == b"RIFFfake-wav-bytes"
    # Student voice is personal data: it must be encrypted at rest...
    assert call["ServerSideEncryption"] == "AES256"
    # ...and no ACL may make it public, unlike the TTS audio path.
    assert "ACL" not in call


@pytest.mark.anyio
async def test_returns_a_key_not_a_public_url(s3):
    """Unlike TTS audio, answers must only be reachable via a presigned URL."""
    key = await s3_service.upload_answer_audio_to_s3(
        audio_bytes=b"x", user_id=7, question_attempt_id=42
    )
    assert not key.startswith("http")
    assert key.startswith("Samvaad-Saathi/answer-audio/")


@pytest.mark.anyio
async def test_key_is_scoped_to_the_student_and_attempt(s3):
    key = await s3_service.upload_answer_audio_to_s3(
        audio_bytes=b"x", user_id=7, question_attempt_id=42
    )
    assert "user_7" in key
    assert "attempt_42" in key


@pytest.mark.anyio
async def test_two_uploads_for_one_attempt_do_not_collide(s3):
    """A retried save must not silently overwrite the earlier recording."""
    first = await s3_service.upload_answer_audio_to_s3(
        audio_bytes=b"one", user_id=7, question_attempt_id=42
    )
    second = await s3_service.upload_answer_audio_to_s3(
        audio_bytes=b"two", user_id=7, question_attempt_id=42
    )
    assert first != second


@pytest.mark.anyio
async def test_the_original_extension_is_preserved(s3):
    key = await s3_service.upload_answer_audio_to_s3(
        audio_bytes=b"x", user_id=7, question_attempt_id=42, extension=".webm"
    )
    assert key.endswith(".webm")


@pytest.mark.anyio
async def test_storage_failure_reports_rather_than_raises(s3):
    """A storage outage must not cost the student their answer.

    Transcription still runs from the temporary file, so returning None loses
    recoverability, not the answer itself. Raising would fail the upload.
    """
    from botocore.exceptions import ClientError

    s3.put_object.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
    key = await s3_service.upload_answer_audio_to_s3(
        audio_bytes=b"x", user_id=7, question_attempt_id=42
    )
    assert key is None


@pytest.mark.anyio
async def test_missing_credentials_report_rather_than_raise(s3):
    """NoCredentialsError is a BotoCoreError, not a ClientError.

    Catching only ClientError let it escape into the upload route, which treats
    any exception as a save failure and answers 503 - so expired credentials
    would have cost students their answers over a storage problem that does not
    affect transcription. CI caught this because it has no AWS credentials.
    """
    from botocore.exceptions import NoCredentialsError

    s3.put_object.side_effect = NoCredentialsError()
    key = await s3_service.upload_answer_audio_to_s3(
        audio_bytes=b"x", user_id=7, question_attempt_id=42
    )
    assert key is None


@pytest.mark.anyio
async def test_endpoint_failure_reports_rather_than_raises(s3):
    from botocore.exceptions import EndpointConnectionError

    s3.put_object.side_effect = EndpointConnectionError(endpoint_url="https://s3")
    key = await s3_service.upload_answer_audio_to_s3(
        audio_bytes=b"x", user_id=7, question_attempt_id=42
    )
    assert key is None


@pytest.mark.anyio
async def test_unconfigured_s3_reports_rather_than_raises(monkeypatch):
    monkeypatch.setattr(s3_service, "s3_client", None)
    key = await s3_service.upload_answer_audio_to_s3(
        audio_bytes=b"x", user_id=7, question_attempt_id=42
    )
    assert key is None


@pytest.mark.anyio
async def test_a_stored_recording_can_be_read_back(s3):
    """The worker has to be able to fetch what the request stored."""
    s3.get_object.return_value = {"Body": mock.MagicMock(read=lambda: b"RIFFstored")}
    audio = await s3_service.fetch_answer_audio_from_s3("some/key.wav")
    assert audio == b"RIFFstored"
    assert s3.get_object.call_args.kwargs["Key"] == "some/key.wav"
