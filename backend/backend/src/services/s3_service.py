import os
import uuid
import logging
import datetime
from typing import Optional
from decouple import config
import boto3
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

    # Generate a predictable filename using the text hash ID.
    s3_file_path = f"Samvaad-Saathi/tts-audio/question_{question_id}.mp3"

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
                    except sqlalchemy.exc.IntegrityError:
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
                        dedup_stmt = sqlalchemy.select(UserResume).where(
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
