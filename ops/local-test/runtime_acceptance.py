"""Activate only recorded local services and verify the real worker expiry path.

Run via product.py's guarded resume-runtime mode. No AI, upload or live AWS calls.
"""
import asyncio
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import uuid

import httpx
import sqlalchemy as sa
from arq.connections import create_pool
from arq.jobs import Job
from src.models.db.resume_storage_job import ResumeStorageJob
from src.models.db.question_attempt import QuestionAttempt
from src.models.db.user import User
from src.repository.database import async_db
from src.worker.settings import redis_settings
import manage

ROOT = Path(__file__).resolve().parents[2]


def recorded_process(mode):
    pid = json.loads((manage.STATE / 'processes.json').read_text())[mode]
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True)
    expected = [str(ROOT / 'ops/local-test/product.py'), str(manage.STATE / 'product.json'), mode]
    if result.returncode or result.stdout.strip().split()[-3:] != expected:
        raise RuntimeError(f'Recorded {mode} does not match the local launcher; no process stopped')
    return pid


async def stop_recorded(mode):
    pid = recorded_process(mode)
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True)
        if result.returncode or result.stdout.strip().startswith('Z'):
            break
        await asyncio.sleep(.1)
    else:
        raise RuntimeError(f'{mode} did not stop gracefully; no replacement launched')


async def restart(mode):
    await stop_recorded(mode)
    manage.launch([ROOT / '.venv/bin/python', ROOT / 'ops/local-test/product.py', manage.STATE / 'product.json', mode], manage.clean_env(), mode)


async def worker_has_active_jobs(redis):
    # arq retains cron claim markers after completion, but atomically removes
    # finished jobs from the queue. A marker alone does not mean active work.
    prefix = b'arq:in-progress:'
    async for key in redis.scan_iter('arq:in-progress:*'):
        if await redis.zscore('arq:queue', key[len(prefix):]) is not None:
            return True
    return False


async def main():
    # Reject URL overrides and non-local backing services without printing secrets.
    config = json.loads((manage.STATE / 'product.json').read_text())
    required = {'POSTGRES_HOST':'127.0.0.1', 'POSTGRES_PORT':'15432',
        'POSTGRES_DB':'samvaad_local', 'REDIS_HOST':'127.0.0.1', 'REDIS_PORT':'16379',
        'REDIS_URL':'', 'AWS_S3_LOCAL_ENDPOINT_URL':'http://127.0.0.1:19000',
        'AWS_S3_BUCKET_NAME':'samvaad-local-files', 'ENVIRONMENT':'DEV',
        'SAMPARK_ENROLLMENT_SYNC_URL':'http://127.0.0.1:8082/sampark/internal/samvaad-enrollment-snapshots'}
    if any(config.get(k) != v for k, v in required.items()):
        raise RuntimeError('Runtime acceptance requires the dedicated local configuration')
    recorded_process('api')
    recorded_process('worker')
    redis = await create_pool(redis_settings)
    fingerprint = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    user_id = None
    try:
        allowed = {'deliver_resume_files', 'synchronize_sampark_enrollments_task',
            'purge_expired_bulk_ats_batches_task', 'sweep_stalled_answer_transcriptions_task'}
        for key in await redis.zrange('arq:queue', 0, -1):
            info = await Job(key.decode(), redis).info()
            if info is None or info.function not in allowed:
                raise RuntimeError('Non-maintenance queued work exists; activation stopped')
        if await worker_has_active_jobs(redis):
            raise RuntimeError('Worker has active work; retry activation when idle')
        async with async_db.get_session() as session:
            pending = (await session.execute(sa.select(sa.func.count()).select_from(ResumeStorageJob).where(ResumeStorageJob.state == 'pending'))).scalar_one()
            audio = (await session.execute(sa.select(sa.func.count()).select_from(QuestionAttempt).where(QuestionAttempt.transcription_status.in_(['pending','processing']), QuestionAttempt.audio_url.is_not(None)))).scalar_one()
            if pending or audio:
                raise RuntimeError('Existing pending file/audio work needs review before activation')
            user_id = (await session.execute(sa.select(User.id).where(User.email == 'student@example.com'))).scalar_one()
        print('PASS: local configuration and recorded processes verified; no queued provider work or pending file/audio work', flush=True)
        await restart('api')
        async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
            for _ in range(40):
                try:
                    response = await client.get('http://127.0.0.1:8000/openapi.json')
                    if response.status_code == 200:
                        schema = response.json()
                        assert 'deliveryStatus' in schema['components']['schemas']['ResumeStorageReceipt']['properties']
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(.5)
            else:
                raise RuntimeError('Updated local API did not become ready')
            password = json.loads((manage.STATE / 'local-secrets.json').read_text())['password']
            tokens = {}
            for name, route in [('student','student-login'), ('facilitator','login')]:
                response = await client.post('http://127.0.0.1:8081/barabari-auth/api/auth/public/v1/' + route,
                    json={'email':name+'@example.com', 'password':password})
                if response.status_code != 200:
                    raise RuntimeError('Local acceptance sign-in failed')
                tokens[name] = response.json()['data']['accessToken']
            # An already-expired synthetic job exercises the real cron without storage calls.
            async with async_db.get_session() as session:
                session.add(ResumeStorageJob(user_id=user_id,file_sha256=fingerprint,
                    payload=b'synthetic expiry only',filename='runtime-test.txt',content_type='text/plain',
                    resume_text='synthetic',state='pending',attempts=0,
                    expires_at=datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(seconds=1)))
                await session.commit()
            url = 'http://127.0.0.1:8000/api/me/resume-storage/' + fingerprint
            response = await client.get(url,headers={'Authorization':'Bearer '+tokens['student']})
            assert response.status_code == 200 and response.json()['deliveryStatus'] == 'expired'
            assert response.headers['cache-control'] == 'no-store'
            other = await client.get(url,headers={'Authorization':'Bearer '+tokens['facilitator']})
            assert other.status_code == 200 and other.json()['deliveryStatus'] is None
            assert (await client.get(url)).status_code in (401,403)
            await restart('worker')
            for _ in range(50):
                async with async_db.get_session() as session:
                    job = await session.get(ResumeStorageJob,(user_id,fingerprint))
                    if job.state == 'expired' and job.payload is None and job.resume_text is None:
                        break
                await asyncio.sleep(.5)
            else:
                raise RuntimeError('Real worker did not clean up the synthetic expired job')
            recorded_process('api')
            recorded_process('worker')
            print('PASS: updated API schema, authenticated live delivery status, account isolation, no-store and unauthenticated denial; restarted real worker cleared expired bytes/text via cron', flush=True)
    finally:
        if user_id is not None:
            async with async_db.get_session() as session:
                await session.execute(sa.delete(ResumeStorageJob).where(ResumeStorageJob.user_id==user_id,ResumeStorageJob.file_sha256==fingerprint))
                await session.commit()
        await redis.aclose()
