"""Real loopback S3-compatible delivery, deduplication and failure/retry acceptance."""
import asyncio
import hashlib
import os
import uuid
import httpx
import sqlalchemy as sa
from arq.connections import create_pool
from src.models.db.user import User
from src.models.db.user_resume import UserResume
from src.models.db.resume_storage_job import ResumeStorageJob
from src.repository.database import async_db
from src.services import s3_service
from src.services.resume_storage_delivery import stage_resume_file, deliver_resume_files
from src.worker.settings import redis_settings
from runtime_acceptance import recorded_process, stop_recorded, worker_has_active_jobs, ROOT
import manage


async def main():
    client = s3_service.s3_client
    bucket = s3_service.BUCKET_NAME
    if client is None or client.meta.endpoint_url != 'http://127.0.0.1:19000' or bucket != 'samvaad-local-files':
        raise RuntimeError('Only the dedicated loopback object store is permitted')
    if redis_settings.host != '127.0.0.1' or redis_settings.port != 16379:
        raise RuntimeError('Only the dedicated local queue is permitted')
    client.head_bucket(Bucket=bucket)
    redis = await create_pool(redis_settings)
    stopped = False
    user_id = None
    try:
        if await worker_has_active_jobs(redis):
            raise RuntimeError('Worker is active; retry acceptance when idle')
        # Pause only the verified local worker to keep cron out of fault-injection windows.
        await stop_recorded('worker')
        stopped = True
        async with async_db.get_session() as session:
            user = User(email=f'object-acceptance-{uuid.uuid4()}@example.invalid',
                        name='Synthetic storage acceptance', password_hash='!disabled')
            session.add(user)
            await session.flush()
            user_id = user.id
            await session.commit()
        async def stage(raw):
            digest = hashlib.sha256(raw).hexdigest()
            await stage_resume_file(raw,user_id,'synthetic.txt','text/plain','synthetic',digest)
            return digest
        async def deliver(digest):
            return await deliver_resume_files(user_id=user_id,fingerprint=digest)
        async def inspect(digest):
            async with async_db.get_session() as session:
                job = await session.get(ResumeStorageJob,(user_id,digest))
                record = (await session.execute(sa.select(UserResume).where(
                    UserResume.user_id==user_id,UserResume.file_sha256==digest))).scalar_one_or_none()
                return job.state, job.attempts, job.payload, job.resume_text, record.s3_key if record else None
        raw = b'Synthetic resume original bytes\nspacing  and unicode: \xe0\xa4\x85'
        digest = await stage(raw)
        assert await deliver(digest) == 1
        state, attempts, payload, text, key = await inspect(digest)
        assert (state,attempts,payload,text) == ('stored',1,None,None) and key
        with client.get_object(Bucket=bucket,Key=key)['Body'] as body:
            assert body.read() == raw
        async with httpx.AsyncClient(trust_env=False) as http:
            response = await http.get(f'http://127.0.0.1:19000/{bucket}/{key}')
            assert response.status_code in (401,403)
        assert await deliver(digest) == 0
        await stage(raw)
        assert await deliver(digest) == 1
        assert (await inspect(digest))[4] == key
        print('PASS: real original-byte upload/read, private anonymous access denial and duplicate delivery reuse',flush=True)
        # Point only this process at a deliberately absent bucket. The real local
        # service returns NoSuchBucket; no server configuration or bucket is changed.
        retry_raw = b'synthetic retry '+uuid.uuid4().bytes
        retry_hash = await stage(retry_raw)
        s3_service.BUCKET_NAME = 'absent-acceptance-'+str(uuid.uuid4())
        assert await deliver(retry_hash) == 1
        state,attempts,payload,text,key = await inspect(retry_hash)
        assert (state,attempts,payload,key) == ('pending',1,retry_raw,None)
        s3_service.BUCKET_NAME = bucket
        assert await deliver(retry_hash) == 1
        state,attempts,payload,text,key = await inspect(retry_hash)
        assert (state,attempts,payload,text) == ('stored',2,None,None) and key
        with client.get_object(Bucket=bucket,Key=key)['Body'] as body:
            assert body.read() == retry_raw
        crash_raw = b'synthetic process crash '+uuid.uuid4().bytes
        crash_hash = await stage(crash_raw)
        child = await asyncio.create_subprocess_exec(
            str(ROOT/'.venv/bin/python'), str(ROOT/'ops/local-test/product.py'),
            str(manage.STATE/'product.json'), 'resume-crash-upload', str(user_id), crash_hash,
            env=manage.clean_env(), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            assert await asyncio.wait_for(child.wait(),30) == 73
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()
        state,attempts,payload,text,key = await inspect(crash_hash)
        assert (state,attempts,payload,key) == ('pending',0,crash_raw,None)
        stable_key = f'Samvaad-Saathi/resumes/user_{user_id}_sha256_{crash_hash}'
        with client.get_object(Bucket=bucket,Key=stable_key)['Body'] as body:
            assert body.read() == crash_raw
        assert await deliver(crash_hash) == 1
        assert (await inspect(crash_hash))[4] == stable_key
        objects = client.list_objects_v2(Bucket=bucket,Prefix=stable_key).get('Contents',[])
        assert [obj['Key'] for obj in objects] == [stable_key]
        assert (await inspect(crash_hash))[:4] == ('stored',1,None,None)
        print('PASS: real child process terminated after object write; committed staging survived and retry registered the same single object key',flush=True)
        failed_raw = b'synthetic exhausted '+uuid.uuid4().bytes
        failed_hash = await stage(failed_raw)
        s3_service.BUCKET_NAME = 'absent-acceptance-'+str(uuid.uuid4())
        for attempt in range(1,4):
            assert await deliver(failed_hash) == 1
            state,attempts,payload,text,key = await inspect(failed_hash)
            assert attempts == attempt and key is None
            assert state == ('failed' if attempt == 3 else 'pending')
        assert payload is None and text is None
        s3_service.BUCKET_NAME = bucket
        assert await deliver(failed_hash) == 0
        print('PASS: real missing-bucket failure, retained bytes, successful retry and three-failure exhaustion with payload removal',flush=True)
    finally:
        s3_service.BUCKET_NAME = bucket
        try:
            if user_id is not None:
                # The newly created user owns this entire prefix, including any
                # object not yet registered if an assertion interrupted the test.
                prefix = f'Samvaad-Saathi/resumes/user_{user_id}_'
                paginator = client.get_paginator('list_objects_v2')
                for page in paginator.paginate(Bucket=bucket,Prefix=prefix):
                    for obj in page.get('Contents',[]):
                        client.delete_object(Bucket=bucket,Key=obj['Key'])
                async with async_db.get_session() as session:
                    await session.execute(sa.delete(User).where(User.id==user_id))
                    await session.commit()
        finally:
            if stopped:
                manage.launch([ROOT/'.venv/bin/python',ROOT/'ops/local-test/product.py',manage.STATE/'product.json','worker'],manage.clean_env(),'worker')
                await asyncio.sleep(2)
                recorded_process('worker')
            await redis.aclose()
    print('PASS: synthetic account/files removed; local worker relaunched',flush=True)


async def crash_after_upload(user_id, fingerprint):
    """Isolated test child: deliberately exits only after its actual local PUT."""
    client = s3_service.s3_client
    if client is None or client.meta.endpoint_url != 'http://127.0.0.1:19000' or s3_service.BUCKET_NAME != 'samvaad-local-files':
        raise RuntimeError('Crash test is local only')
    async with async_db.get_session() as session:
        user = await session.get(User,user_id)
        job = await session.get(ResumeStorageJob,(user_id,fingerprint))
        if user is None or not user.email.startswith('object-acceptance-') or not user.email.endswith('@example.invalid'):
            raise RuntimeError('Crash test requires its synthetic account')
        if job is None or not job.payload.startswith(b'synthetic process crash '):
            raise RuntimeError('Crash test requires its synthetic payload')
    original = s3_service.upload_resume_to_s3
    def write_then_exit(*args, **kwargs):
        key = original(*args, **kwargs)
        if key:
            os._exit(73)
        raise RuntimeError('Crash test upload failed before interruption')
    s3_service.upload_resume_to_s3 = write_then_exit
    await deliver_resume_files(user_id=user_id,fingerprint=fingerprint)
    raise RuntimeError('Expected crash boundary was not reached')
