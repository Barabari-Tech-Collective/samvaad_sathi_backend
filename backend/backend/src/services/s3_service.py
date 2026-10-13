import os
import uuid
import logging
import datetime
from typing import Optional
from decouple import config
import boto3
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Initialize boto3 client lazily or handle missing creds gracefully
try:
    # Explicitly pass credentials from .env via config()
    s3_client = boto3.client(
        's3',
        aws_access_key_id=config("AWS_ACCESS_KEY_ID", default=None),
        aws_secret_access_key=config("AWS_SECRET_ACCESS_KEY", default=None),
        region_name=config("AWS_REGION", default="ap-south-1")
    )
    BUCKET_NAME = config("AWS_S3_BUCKET_NAME", default="barabari-edtech-service-staging")
except Exception as e:
    logger.warning(f"Could not initialize S3 client: {e}")
    s3_client = None
    BUCKET_NAME = None


def tts_audio_s3_key(question_id: int) -> str:
    """
    Predictable S3 key for a question's TTS audio, scoped to the currently
    configured ElevenLabs voice.

    Scoping by voice means that when ELEVENLABS_VOICE_ID changes, previously
    generated audio no longer matches the current key and gets regenerated
    instead of being served forever under a stale voice - see
    interviews_v2._prepare_audio_for_questions, which uses this same
    function to decide whether a question needs new TTS audio.
    """
    import hashlib

    from src.config.manager import settings

    voice_tag = hashlib.md5(settings.ELEVENLABS_VOICE_ID.encode()).hexdigest()[:8]
    return f"Samvaad-Saathi/tts-audio/question_{question_id}_{voice_tag}.mp3"


def upload_audio_to_s3(audio_bytes: bytes, question_id: int) -> Optional[str]:
    """
    Uploads the TTS audio byte stream to the S3 bucket.

    Args:
        audio_bytes: The raw MP3 byte data from ElevenLabs.
        question_id: The ID of the question (used for naming the file).

    Returns:
        The public S3 URL of the uploaded file, or None if the upload failed.
    """
    if not s3_client or not BUCKET_NAME:
        logger.error("S3 client is not configured. Ensure AWS credentials are in .env.")
        return None

    # Generate a predictable filename using the text hash ID, scoped to the
    # active voice so a voice change can't leave old and new audio mixed.
    s3_file_path = tts_audio_s3_key(question_id)

    try:
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_file_path,
            Body=audio_bytes,
            ContentType="audio/mpeg",
        )
        
        region = config("AWS_REGION", default="ap-south-1")
        # Construct the public URL
        public_url = f"https://{BUCKET_NAME}.s3.{region}.amazonaws.com/{s3_file_path}"
        logger.info(f"Successfully uploaded TTS audio to S3: {public_url}")
        
        return public_url
        
    except ClientError as e:
        logger.error(f"Failed to upload audio to S3: {e}")
        return None

def upload_resume_to_s3(file_bytes: bytes, user_id: int, filename: str, content_type: str = "application/pdf") -> Optional[str]:
    """
    Uploads a resume file byte stream to the S3 bucket.
    
    Args:
        file_bytes: The raw file data.
        user_id: The ID of the user uploading the resume.
        filename: The original filename.
        content_type: The MIME type of the file.
        
    Returns:
        The S3 object key if successful, or None if failed.
    """
    if not s3_client or not BUCKET_NAME:
        logger.error("S3 client is not configured. Ensure AWS credentials are in .env.")
        return None

    unique_suffix = str(uuid.uuid4())[:8]
    # Keep the original extension
    import os
    _, ext = os.path.splitext(filename)
    if not ext:
        ext = ".pdf" if "pdf" in content_type.lower() else ".txt"
        
    # The key (path) inside the bucket
    s3_object_key = f"Samvaad-Saathi/resumes/user_{user_id}_{unique_suffix}{ext}"

    try:
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_object_key,
            Body=file_bytes,
            ContentType=content_type,
        )
        logger.info(f"Successfully uploaded resume to S3: {s3_object_key}")
        return s3_object_key
        
    except ClientError as e:
        logger.error(f"Failed to upload resume to S3: {e}")
        return None

#: Prefix for student answer recordings. Kept distinct from tts-audio/ and
#: resumes/ so a bucket lifecycle or retention rule can target voice recordings
#: specifically without touching anything else.
ANSWER_AUDIO_PREFIX = "Samvaad-Saathi/answer-audio"


def answer_audio_s3_key(user_id: int, question_attempt_id: int, extension: str = ".wav") -> str:
    """Stable, non-guessable key for one student's answer recording."""
    return (
        f"{ANSWER_AUDIO_PREFIX}/user_{user_id}/attempt_{question_attempt_id}"
        f"_{uuid.uuid4().hex[:12]}{extension}"
    )


async def upload_answer_audio_to_s3(
    audio_bytes: bytes,
    user_id: int,
    question_attempt_id: int,
    extension: str = ".wav",
) -> Optional[str]:
    """Stores a student's answer recording and returns its object key.

    Answer audio was previously written to a temporary file that the request
    deleted on its way out, so a failed transcription lost the recording for
    good, an answer could never be re-transcribed after a provider outage, and
    a disputed score could not be checked against what the student actually
    said. Storing it durably is also what allows transcription to move off the
    request path at all - a background worker needs something to fetch.

    Returns an object key rather than a URL, like resumes and unlike TTS audio:
    this is personal data and must only ever be reachable through a short-lived
    presigned URL, never a public link.

    boto3 is synchronous, so the call is run in a worker thread. Awaiting it on
    the event loop would stall every other request in this process for the
    duration of the upload - and with one server worker, that is all of them.
    """
    if not s3_client or not BUCKET_NAME:
        logger.error("S3 client is not configured; cannot store answer audio.")
        return None

    import asyncio

    object_key = answer_audio_s3_key(user_id, question_attempt_id, extension)

    def _put() -> None:
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=object_key,
            Body=audio_bytes,
            ContentType="audio/wav",
            # Student voice recordings are personal data; encrypt at rest.
            ServerSideEncryption="AES256",
        )

    try:
        await asyncio.get_running_loop().run_in_executor(None, _put)
        logger.info(
            "Stored answer audio | QuestionAttempt=%s | Key=%s",
            question_attempt_id,
            object_key,
        )
        return object_key
    except Exception as error:  # noqa: BLE001
        # Deliberately broad. Catching only ClientError let NoCredentialsError
        # and EndpointConnectionError - both BotoCoreError, not ClientError -
        # escape into the upload route, which treats any exception as a save
        # failure and answers 503. Missing or expired credentials would then
        # cost a student their answer over a storage problem that does not
        # affect transcription at all.
        logger.error(
            "Failed to store answer audio | QuestionAttempt=%s | %s",
            question_attempt_id,
            error,
        )
        return None


#: Prefix for Bulk ATS candidate resumes. Kept distinct from resumes/ (the
#: student onboarding/ATS-final flow) since these are recruiter-uploaded,
#: retained for a batch's lifetime rather than a user's, and never linked to
#: a student account.
BULK_ATS_RESUME_PREFIX = "Samvaad-Saathi/bulk-ats-resumes"


def bulk_ats_resume_s3_key(batch_id: int, filename: str) -> str:
    unique_suffix = str(uuid.uuid4())[:12]
    _, ext = os.path.splitext(filename)
    return f"{BULK_ATS_RESUME_PREFIX}/batch_{batch_id}/{unique_suffix}{ext or '.pdf'}"


def upload_bulk_ats_resume_to_s3(file_bytes: bytes, batch_id: int, filename: str, content_type: str) -> Optional[str]:
    if not s3_client or not BUCKET_NAME:
        logger.error("S3 client is not configured. Ensure AWS credentials are in .env.")
        return None

    object_key = bulk_ats_resume_s3_key(batch_id, filename)
    try:
        s3_client.put_object(Bucket=BUCKET_NAME, Key=object_key, Body=file_bytes, ContentType=content_type)
        return object_key
    except ClientError as e:
        logger.error(f"Failed to upload bulk ATS resume to S3: {e}")
        return None


async def fetch_bulk_ats_resume_from_s3(object_key: str) -> Optional[bytes]:
    """Reads back a stored Bulk ATS resume, for the scoring worker task."""
    if not s3_client or not BUCKET_NAME:
        logger.error("S3 client is not configured; cannot read bulk ATS resume.")
        return None

    import asyncio

    def _get() -> bytes:
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=object_key)
        return response["Body"].read()

    try:
        return await asyncio.get_running_loop().run_in_executor(None, _get)
    except Exception as error:  # noqa: BLE001
        logger.error("Failed to read bulk ATS resume | Key=%s | %s", object_key, error)
        return None


async def fetch_answer_audio_from_s3(object_key: str) -> Optional[bytes]:
    """Reads back a stored answer recording, for the transcription worker."""
    if not s3_client or not BUCKET_NAME:
        logger.error("S3 client is not configured; cannot read answer audio.")
        return None

    import asyncio

    def _get() -> bytes:
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=object_key)
        return response["Body"].read()

    try:
        return await asyncio.get_running_loop().run_in_executor(None, _get)
    except Exception as error:  # noqa: BLE001
        # Broad for the same reason as the upload above: credential and
        # endpoint failures are BotoCoreError rather than ClientError, and the
        # worker must record a transcription failure rather than crash.
        logger.error("Failed to read answer audio | Key=%s | %s", object_key, error)
        return None


def get_presigned_url(object_key: str, expiration: int = 3600) -> Optional[str]:
    """
    Generates a short-lived presigned URL to securely download a file.
    
    Args:
        object_key: The S3 object key (e.g. 'Samvaad-Saathi/resumes/...').
        expiration: How many seconds the URL should be valid for (default 1 hr).
        
    Returns:
        The presigned URL string, or None if failed.
    """
    if not s3_client or not BUCKET_NAME:
        logger.error("S3 client is not configured. Ensure AWS credentials are in .env.")
        return None

    try:
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': BUCKET_NAME, 'Key': object_key},
            ExpiresIn=expiration
        )
        return url
    except ClientError as e:
        logger.error(f"Failed to generate presigned URL: {e}")
        return None

async def _background_upload_and_enforce_limit(
    file_bytes: bytes, 
    user_id: int, 
    filename: str, 
    content_type: str, 
    resume_text: str | None, 
    source: str,
    size_bytes: int | None = None,
    file_sha256: str | None = None,
):
    """
    Background task to upload a resume and save the key to the UserResume table.
    Enforces a global limit of 3 resumes per user. Oldest resume is deleted if limit is exceeded.
    """
    import asyncio
    import logging
    import sqlalchemy
    from src.repository.database import async_db
    from src.models.db.user_resume import UserResume

    logger = logging.getLogger(__name__)

    try:
        # Deduplication check before uploading to S3
        if file_sha256:
            async with async_db.get_session() as session:
                dedup_stmt = sqlalchemy.select(UserResume).where(
                    UserResume.user_id == user_id, 
                    UserResume.file_sha256 == file_sha256
                )
                dedup_result = await session.execute(dedup_stmt)
                existing_resume = dedup_result.scalar_one_or_none()
                if existing_resume:
                    # Dedup: Identical PDF already exists, update timestamp to make it newest
                    existing_resume.created_at = datetime.datetime.now(datetime.timezone.utc)
                    await session.commit()
                    logger.info(f"Dedup matched! Returning existing S3 key for user {user_id}")
                    return existing_resume.s3_key

        loop = asyncio.get_running_loop()
        s3_key = await loop.run_in_executor(
            None, 
            upload_resume_to_s3, 
            file_bytes, user_id, filename, content_type
        )
        
        if s3_key:
            s3_keys_to_delete = []
            try:
                async with async_db.get_session() as session:
                    # Truncate filename to prevent DB insert errors causing S3 object leaks
                    safe_filename = filename[:256] if filename else "resume.pdf"

                    # 1. Insert the new resume
                    new_resume = UserResume(
                        user_id=user_id,
                        s3_key=s3_key,
                        filename=safe_filename,
                        resume_text=resume_text,
                        source=source,
                        content_type=content_type,
                        size_bytes=size_bytes,
                        file_sha256=file_sha256
                    )
                    try:
                        session.add(new_resume)
                        await session.flush()
                    except IntegrityError:
                        # Raced with another upload of the same file. Treat as deduplication success.
                        await session.rollback()
                        logger.info(f"IntegrityError: Concurrent upload detected for user {user_id}. Using existing.")
                        
                        # Clean up the orphaned S3 object we just uploaded
                        if s3_client and BUCKET_NAME:
                            try:
                                await loop.run_in_executor(
                                    None,
                                    lambda k: s3_client.delete_object(Bucket=BUCKET_NAME, Key=k),
                                    s3_key
                                )
                            except Exception as e:
                                logger.error(f"Failed to cleanup S3 object {s3_key} after IntegrityError: {e}")
                        
                        # Query the existing file that caused the conflict and return its key
                        dedup_stmt = select(UserResume).where(
                            UserResume.user_id == user_id, 
                            UserResume.file_sha256 == file_sha256
                        )
                        dedup_result = await session.execute(dedup_stmt)
                        existing_resume = dedup_result.scalar_one_or_none()
                        if existing_resume:
                            existing_resume.created_at = datetime.datetime.now(datetime.timezone.utc)
                            await session.commit()
                            return existing_resume.s3_key
                        else:
                            raise

                    # 2. Enforce the limit of 3 with row-level locks to prevent race conditions
                    stmt = (
                        sqlalchemy.select(UserResume)
                        .where(UserResume.user_id == user_id)
                        .order_by(UserResume.created_at.desc(), UserResume.id.desc())
                        .with_for_update()
                    )
                    result = await session.execute(stmt)
                    user_resumes = result.scalars().all()

                    if len(user_resumes) > 3:
                        # Find all resumes beyond the most recent 3
                        resumes_to_delete = user_resumes[3:]
                        for old_resume in resumes_to_delete:
                            s3_keys_to_delete.append(old_resume.s3_key)
                            # Delete from database first
                            await session.delete(old_resume)

                    await session.commit()
            except Exception as db_err:
                # DB transaction failed. Clean up the newly uploaded S3 object to prevent leaks.
                if s3_client and BUCKET_NAME:
                    try:
                        await loop.run_in_executor(
                            None,
                            lambda k: s3_client.delete_object(Bucket=BUCKET_NAME, Key=k),
                            s3_key
                        )
                        logger.info(f"Cleaned up orphaned S3 object after DB failure: {s3_key}")
                    except Exception as s3_cleanup_err:
                        logger.error(f"Failed to clean up orphaned S3 object {s3_key}: {s3_cleanup_err}")
                raise db_err

            # DB commit succeeded. Now perform best-effort S3 cleanup for old resumes.
            for old_key in s3_keys_to_delete:
                if s3_client and BUCKET_NAME and old_key:
                    try:
                        await loop.run_in_executor(
                            None,
                            lambda k: s3_client.delete_object(Bucket=BUCKET_NAME, Key=k),
                            old_key
                        )
                        logger.info(f"Deleted old resume from S3: {old_key}")
                    except Exception as e:
                        logger.error(f"Failed to delete old resume from S3: {e}")

            logger.info(f"Successfully processed _background_upload_and_enforce_limit for user {user_id}")
            return s3_key
        else:
            raise Exception("S3 upload returned None")
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Failed background S3 resume upload: {e}")
        raise e
