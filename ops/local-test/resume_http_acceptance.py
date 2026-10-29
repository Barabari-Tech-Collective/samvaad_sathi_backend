"""ASGI HTTP contract test with real local auth/DB and synthetic extraction only."""
import asyncio
import hashlib
import json
from pathlib import Path
import uuid
import httpx
import sqlalchemy as sa
from fastapi import FastAPI
from src.api.routes import resume
from src.models.db.resume_operation import ResumeOperation
from src.repository.database import async_db


async def main():
    state = Path(__file__).resolve().parents[3] / "samvaad-local-test"
    password = json.loads((state / "local-secrets.json").read_text())["password"]
    tokens = {}
    async with httpx.AsyncClient(trust_env=False, timeout=30) as auth:
        for name, route in (("student", "student-login"), ("facilitator", "login")):
            response = await auth.post("http://127.0.0.1:8081/barabari-auth/api/auth/public/v1/" + route,
                json={"email": name + "@example.com", "password": password})
            if response.status_code != 200:
                raise RuntimeError("Local test identity login failed")
            tokens[name] = response.json()["data"]["accessToken"]
    app = FastAPI()
    app.include_router(resume.router, prefix="/api")
    key = str(uuid.uuid4())
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0
    original = resume._extract_resume_content
    async def synthetic(background_tasks, file, current_user, user_repo):
        nonlocal calls
        calls += 1
        raw = await file.read()
        started.set()
        await release.wait()
        return {"filename":file.filename, "content_type":file.content_type, "size":len(raw),
                "text_length":len(raw), "preview":"synthetic acceptance", "validated":{},
                "message":"Synthetic extraction only", "saved":True,
                "file_sha256":hashlib.sha256(raw).hexdigest()}
    resume._extract_resume_content = synthetic
    request = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test") as client:
            headers = {"Authorization":"Bearer " + tokens["student"], "Idempotency-Key":key}
            def upload(content=b"synthetic resume"):
                return client.post("/api/extract-resume", headers=headers, files={"file":("resume.txt",content,"text/plain")})
            denied = await client.post("/api/extract-resume",files={"file":("resume.txt",b"test","text/plain")})
            assert denied.status_code in (401,403)
            invalid = await client.post("/api/extract-resume", headers={**headers, "Idempotency-Key":"invalid"}, files={"file":("resume.txt",b"test","text/plain")})
            assert invalid.status_code == 422 and calls == 0
            unknown = await client.get(f"/api/me/resume-operations/{uuid.uuid4()}", headers=headers)
            assert unknown.status_code == 404
            forged = await client.get(f"/api/me/resume-operations/{key}", headers={"Authorization":"Bearer invalid"})
            assert forged.status_code == 401
            request = asyncio.create_task(upload())
            await asyncio.wait_for(started.wait(), 20)
            duplicate = await upload()
            assert duplicate.status_code == 409
            other = await client.get(f"/api/me/resume-operations/{key}",headers={"Authorization":"Bearer " + tokens["facilitator"]})
            assert other.status_code == 404
            status = await client.get(f"/api/me/resume-operations/{key}",headers=headers)
            assert status.status_code == 200 and status.json()["state"] == "started"
            unresolved_recovery = await client.post(f"/api/me/resume-operations/{key}/recovery", headers=headers)
            assert unresolved_recovery.status_code == 409
            unresolved_storage = await client.post(f"/api/me/resume-operations/{key}/restore-file", headers=headers, files={"file":("resume.txt",b"synthetic resume","text/plain")})
            assert unresolved_storage.status_code == 409
            release.set()
            first = await request
            assert first.status_code == 202
            assert first.json()["fileSha256"] == hashlib.sha256(b"synthetic resume").hexdigest()
            replay = await upload()
            assert replay.status_code == 202 and replay.json() == first.json()
            assert (await upload(b"different bytes")).status_code == 409
            status = await client.get(f"/api/me/resume-operations/{key}",headers=headers)
            assert status.json()["state"] == "completed" and status.json()["dataSaved"] is True
            assert status.headers["cache-control"] == "no-store"
            assert "preview" not in status.json()
            storage = await client.get("/api/me/resume-storage/"+first.json()["fileSha256"],headers=headers)
            assert storage.json()["status"] == "unconfirmed"  # Synthetic work never stored a file.
            async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", trust_env=False, timeout=30) as live:
                live_status = await live.get(f"/api/me/resume-operations/{key}", headers=headers)
                assert live_status.status_code == 200 and live_status.json() == status.json()
                saved_recovery = await live.post(f"/api/me/resume-operations/{key}/recovery", headers=headers)
                assert saved_recovery.status_code == 409
                wrong_storage = await live.post(f"/api/me/resume-operations/{key}/restore-file", headers=headers, files={"file":("resume.txt",b"different bytes","text/plain")})
                assert wrong_storage.status_code == 409
                live_other = await live.get(f"/api/me/resume-operations/{key}", headers={"Authorization":"Bearer " + tokens["facilitator"]})
                assert live_other.status_code == 404
                live_replay = await live.post("/api/extract-resume", headers=headers, files={"file":("resume.txt",b"synthetic resume","text/plain")})
                assert live_replay.status_code == 202 and live_replay.json() == first.json()
            assert calls == 1
            print("PASS: real local authentication, ASGI multipart/header contract, concurrent 409, account isolation, status, camelCase replay independent storage, live-server status/isolation and live replay; one synthetic extraction")
    finally:
        release.set()
        if request is not None:
            await asyncio.gather(request, return_exceptions=True)
        resume._extract_resume_content = original
        async with async_db.get_session() as session:
            await session.execute(sa.delete(ResumeOperation).where(ResumeOperation.request_key == key))
            await session.commit()
