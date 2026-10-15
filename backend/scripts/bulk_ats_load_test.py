#!/usr/bin/env python3
"""Exercise the real 100-resume Bulk ATS API lifecycle and report latency."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

import httpx

TERMINAL = {"completed", "needs_review", "failed"}


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * pct)))
    return round(ordered[index], 3)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixtures", help="Directory containing PDF/DOCX test resumes")
    parser.add_argument(
        "--base-url",
        default=os.getenv("BULK_ATS_BASE_URL", "http://localhost:8000/api"),
    )
    parser.add_argument("--token-env", default="BULK_ATS_TEST_TOKEN")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--deepseek-input-cost-per-million", type=float, default=0.0)
    parser.add_argument("--deepseek-output-cost-per-million", type=float, default=0.0)
    args = parser.parse_args()
    token = os.getenv(args.token_env, "")
    if not token:
        raise SystemExit(f"Set {args.token_env} to a recruiter/admin bearer token")
    paths = [
        path
        for path in sorted(Path(args.fixtures).iterdir())
        if path.suffix.lower() in {".pdf", ".docx"}
    ]
    if not paths:
        raise SystemExit("No PDF/DOCX fixtures found")
    requested_count = min(args.count, 100)
    if len(paths) < requested_count:
        raise SystemExit(
            f"Need {requested_count} unique fixtures because per-batch content-hash deduplication is enabled; found {len(paths)}"
        )
    selected = paths[:requested_count]
    headers = {"Authorization": f"Bearer {token}"}
    started = time.monotonic()
    with httpx.Client(
        base_url=args.base_url.rstrip("/"), headers=headers, timeout=120
    ) as client:
        batch_response = client.post(
            "/bulk-ats/batches",
            data={
                "name": f"Load test {int(time.time())}",
                "role_name": "Backend Engineer",
                "role_description": "Build reliable APIs and services. Responsibilities include delivery, customer impact, and measurable outcomes.",
                "required_skills": "Python, APIs",
                "preferred_skills": "FastAPI, PostgreSQL",
                "retention_days": "1",
            },
        )
        batch_response.raise_for_status()
        batch_id = batch_response.json()["id"]
        handles = [path.open("rb") for path in selected]
        try:
            files = [
                (
                    "files",
                    (
                        f"{index:03d}-{path.name}",
                        handle,
                        (
                            "application/pdf"
                            if path.suffix.lower() == ".pdf"
                            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        ),
                    ),
                )
                for index, (path, handle) in enumerate(zip(selected, handles))
            ]
            upload = client.post(f"/bulk-ats/batches/{batch_id}/resumes", files=files)
            upload.raise_for_status()
        finally:
            for handle in handles:
                handle.close()
        accepted = upload.json()["accepted"]
        queued_at = time.monotonic()
        candidate_ids = {item["candidateId"] for item in accepted}
        terminal_at: dict[int, float] = {}
        counts: dict[str, int] = {}
        max_pending = len(candidate_ids)
        while time.monotonic() - started < args.timeout:
            response = client.get(
                f"/bulk-ats/batches/{batch_id}/candidates",
                params={"limit": 200, "sort": "created"},
            )
            response.raise_for_status()
            candidates = response.json()["candidates"]
            counts = {}
            for candidate in candidates:
                counts[candidate["status"]] = counts.get(candidate["status"], 0) + 1
                if candidate["id"] in candidate_ids and candidate["status"] in TERMINAL:
                    terminal_at.setdefault(candidate["id"], time.monotonic())
            max_pending = max(max_pending, len(candidate_ids) - len(terminal_at))
            if len(terminal_at) == len(candidate_ids):
                break
            time.sleep(args.poll_seconds)
        prompt_tokens = 0
        completion_tokens = 0
        jev_unavailable = 0
        deepseek_fallbacks = 0
        for candidate_id in terminal_at:
            detail = client.get(f"/bulk-ats/candidates/{candidate_id}")
            detail.raise_for_status()
            payload = detail.json()
            jev_unavailable += int("jev_unavailable" in payload.get("warnings", []))
            meta = (payload.get("deterministicReport") or {}).get(
                "candidateBriefMeta"
            ) or {}
            usage = meta.get("usage") or {}
            prompt_tokens += usage.get("promptTokens") or 0
            completion_tokens += usage.get("completionTokens") or 0
            deepseek_fallbacks += int(meta.get("source") != "deepseek")
    latencies = [ended - queued_at for ended in terminal_at.values()]
    estimated_cost = (
        prompt_tokens * args.deepseek_input_cost_per_million
        + completion_tokens * args.deepseek_output_cost_per_million
    ) / 1_000_000
    print(
        json.dumps(
            {
                "batchId": batch_id,
                "requested": len(selected),
                "accepted": len(accepted),
                "terminal": len(terminal_at),
                "statusCounts": counts,
                "batchDurationSeconds": round(time.monotonic() - started, 3),
                "candidateLatencySeconds": {
                    "p50": percentile(latencies, 0.50),
                    "p95": percentile(latencies, 0.95),
                    "max": round(max(latencies), 3) if latencies else None,
                },
                "maxPendingCandidates": max_pending,
                "providerFailures": {
                    "jevUnavailable": jev_unavailable,
                    "deepseekFallbacks": deepseek_fallbacks,
                },
                "deepseekUsage": {
                    "promptTokens": prompt_tokens,
                    "completionTokens": completion_tokens,
                    "estimatedCost": round(estimated_cost, 6),
                    "estimatedCostPerTerminalCandidate": (
                        round(estimated_cost / len(terminal_at), 6)
                        if terminal_at
                        else None
                    ),
                },
                "timedOut": len(terminal_at) != len(candidate_ids),
                "notes": [
                    "maxPendingCandidates is an API-visible queue-depth proxy, not the Redis queue length.",
                    "Peak worker memory and provider-internal retry counts require deployment metrics (for example CloudWatch/Prometheus).",
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
