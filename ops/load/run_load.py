"""Concurrent students and staff against a LOCAL backend (see ops/load/README.md).

    python ops/load/run_load.py --students 100 --staff 100 --minutes 3

Students: open Home (profile, next step, recent interviews, notices), then poll an
answer's transcript every 1.5 s for 30 s, as the interview screen does, then think
for 20-40 s. Staff: open the Overview (every dashboard request in parallel, like the
page), then think for 15-45 s. Reports p50/p95/max per request and any errors.
AI calls are not exercised: there is no provider key in a local run.
"""

import argparse
import asyncio
import base64
import os
import random
import statistics
import subprocess
import time
from collections import defaultdict

import httpx
from jose import jwt

BASE = os.environ.get("LOAD_BASE_URL", "http://127.0.0.1:8088/api")
SECRET = base64.b64decode(os.environ["AUTH_SERVICE_JWT_SECRET"])
STAFF_PAGE = [
    "/v2/analytics/dashboard/overview",
    "/v2/analytics/dashboard/interviews-per-day",
    "/v2/analytics/dashboard/active-users-trend",
    "/v2/analytics/dashboard/new-students-trend",
    "/v2/analytics/dashboard/top-roles",
    "/v2/analytics/dashboard/top-colleges",
    "/v2/analytics/dashboard/students-per-college",
    "/v2/analytics/dashboard/score-distribution",
    "/v2/analytics/dashboard/recent-interviews",
    "/v2/analytics/dashboard/recent-students",
    "/v2/analytics/dashboard/attention-required",
    "/v2/analytics/dashboard/pulse",
]
timings: dict[str, list[float]] = defaultdict(list)
errors: dict[str, int] = defaultdict(int)


def token(email: str, role: str) -> str:
    now = int(time.time())
    claims = {"sub": email, "role": role, "userUniqueId": email, "iat": now, "exp": now + 7200}
    return jwt.encode(claims, SECRET, algorithm="HS256")


async def get(client: httpx.AsyncClient, path: str, label: str | None = None):
    start = time.perf_counter()
    try:
        response = await client.get(path)
        ok = response.status_code < 400
    except httpx.HTTPError:
        ok, response = False, None
    timings[label or path].append(time.perf_counter() - start)
    if not ok:
        errors[f"{label or path} {getattr(response, 'status_code', 'network')}"] += 1
    return response


async def student(n: int, deadline: float, attempt_ids: dict[str, int]):
    email = f"load-student-{n}@samvaad-load.org"
    headers = {"Authorization": f"Bearer {token(email, 'STUDENT')}"}
    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=30) as client:
        await asyncio.sleep(random.uniform(0, 10))
        while time.monotonic() < deadline:
            await asyncio.gather(
                get(client, "/me"),
                get(client, "/v2/student-home/today"),
                get(client, "/interviews-with-summary?limit=3"),
                get(client, "/v2/integrity/me/notice-count"),
            )
            attempt = attempt_ids.get(email)
            if attempt:
                for _ in range(20):
                    await get(client, f"/audio/attempts/{attempt}/transcription", "transcript poll")
                    await asyncio.sleep(1.5)
            await asyncio.sleep(random.uniform(20, 40))


async def staff(n: int, deadline: float):
    headers = {"Authorization": f"Bearer {token(f'load-staff-{n}@samvaad-load.org', 'ADMIN')}"}
    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=30) as client:
        await asyncio.sleep(random.uniform(0, 10))
        while time.monotonic() < deadline:
            await asyncio.gather(*(get(client, p) for p in STAFF_PAGE))
            await asyncio.sleep(random.uniform(15, 45))


def give_each_student_an_answer(count: int) -> dict[str, int]:
    """Provisioned students get one answer each, so polling reads a real row."""
    sql = f"""
    WITH s AS (SELECT id, email FROM "user" WHERE email LIKE 'load-student-%' ORDER BY id LIMIT {count}),
    iv AS (INSERT INTO interview (user_id, track, status, interview_mode)
           SELECT id, 'Data analyst', 'active', 'practice' FROM s RETURNING id, user_id),
    qa AS (INSERT INTO question_attempt (interview_id, question_text, transcription_status)
           SELECT id, 'Tell me about yourself', 'processing' FROM iv RETURNING id, interview_id)
    SELECT s.email, qa.id FROM qa JOIN iv ON iv.id = qa.interview_id JOIN s ON s.id = iv.user_id;
    """
    out = subprocess.run(
        ["psql", "-h", "127.0.0.1", "-U", "ci", "-d", os.environ["POSTGRES_DB"], "-tA", "-F", ",", "-c", sql],
        check=True, capture_output=True, text=True,
    ).stdout
    return {line.split(",")[0]: int(line.split(",")[1]) for line in out.splitlines() if "," in line}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--students", type=int, default=100)
    parser.add_argument("--staff", type=int, default=100)
    parser.add_argument("--minutes", type=float, default=3)
    args = parser.parse_args()

    # Sign every account in once so the backend creates it.
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        for n in range(args.students):
            await client.get("/me", headers={"Authorization": f"Bearer {token(f'load-student-{n}@samvaad-load.org', 'STUDENT')}"})
    attempts = give_each_student_an_answer(args.students)

    deadline = time.monotonic() + args.minutes * 60
    started = time.perf_counter()
    await asyncio.gather(
        *(student(n, deadline, attempts) for n in range(args.students)),
        *(staff(n, deadline) for n in range(args.staff)),
    )
    elapsed = time.perf_counter() - started
    total = sum(len(v) for v in timings.values())
    print(f"{args.students} students, {args.staff} staff, {elapsed:.0f}s, {total} requests, {total / elapsed:.1f}/s")
    print(f"{'request':58} {'n':>6} {'p50 ms':>8} {'p95 ms':>8} {'max ms':>8}")
    for path, values in sorted(timings.items(), key=lambda kv: -statistics.quantiles(kv[1], n=20)[-1] if len(kv[1]) > 1 else 0):
        values.sort()
        p95 = values[int(len(values) * 0.95) - 1] if len(values) > 1 else values[0]
        print(f"{path:58} {len(values):6} {statistics.median(values) * 1000:8.0f} {p95 * 1000:8.0f} {values[-1] * 1000:8.0f}")
    print("errors:", dict(errors) or "none")


if __name__ == "__main__":
    asyncio.run(main())
