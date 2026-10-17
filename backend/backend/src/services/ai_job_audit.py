"""Versioned, privacy-minimised audit trail for production AI calls."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

import sqlalchemy

from src.models.db.ai_job import AIJob
from src.repository.database import async_db

logger = logging.getLogger(__name__)


def stable_json_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class AIJobSpec:
    job_type: str
    idempotency_key: str
    input_hash: str
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    parameters: dict[str, Any]
    interview_id: int | None = None
    rubric_version: str | None = None


@dataclass(frozen=True)
class AIJobReplay:
    result_payload: dict[str, Any]
    latency_ms: int | None
    model: str


async def begin_ai_job(spec: AIJobSpec) -> AIJobReplay | None:
    """Create/reset an audit row, or return a completed idempotent result.

    Audit persistence is best-effort by design: a temporary audit-table issue
    must not block a student from receiving the curated fallback.  The error log
    contains identifiers and exception type only, never prompt/input content.
    """

    try:
        async with async_db.get_session() as session:
            existing = (
                await session.execute(
                    sqlalchemy.select(AIJob).where(
                        AIJob.idempotency_key == spec.idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if (
                existing is not None
                and existing.status == "completed"
                and existing.input_hash == spec.input_hash
                and isinstance(existing.result_payload, dict)
            ):
                return AIJobReplay(
                    result_payload=existing.result_payload,
                    latency_ms=existing.latency_ms,
                    model=existing.model,
                )

            if existing is None:
                existing = AIJob(
                    interview_id=spec.interview_id,
                    job_type=spec.job_type,
                    idempotency_key=spec.idempotency_key,
                    input_hash=spec.input_hash,
                    status="running",
                    provider=spec.provider,
                    model=spec.model,
                    prompt_version=spec.prompt_version,
                    schema_version=spec.schema_version,
                    rubric_version=spec.rubric_version,
                    parameters=spec.parameters,
                    attempt_count=1,
                )
                session.add(existing)
            else:
                existing.interview_id = spec.interview_id
                existing.input_hash = spec.input_hash
                existing.status = "running"
                existing.provider = spec.provider
                existing.model = spec.model
                existing.prompt_version = spec.prompt_version
                existing.schema_version = spec.schema_version
                existing.rubric_version = spec.rubric_version
                existing.parameters = spec.parameters
                existing.result_payload = None
                existing.result_hash = None
                existing.latency_ms = None
                existing.error_code = None
                existing.completed_at = None
                existing.attempt_count = int(existing.attempt_count or 0) + 1
            await session.commit()
    except Exception as exc:
        logger.error(
            "AI job audit start failed | job_type=%s | key=%s | error_type=%s",
            spec.job_type,
            spec.idempotency_key,
            type(exc).__name__,
        )
    return None


async def finish_ai_job(
    spec: AIJobSpec,
    *,
    status: str,
    latency_ms: int,
    result_payload: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> None:
    try:
        async with async_db.get_session() as session:
            job = (
                await session.execute(
                    sqlalchemy.select(AIJob).where(
                        AIJob.idempotency_key == spec.idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if job is None:
                return
            job.status = status
            job.latency_ms = max(0, latency_ms)
            job.result_payload = result_payload
            job.result_hash = (
                stable_json_hash(result_payload) if result_payload else None
            )
            job.error_code = error_code
            job.completed_at = datetime.datetime.now(datetime.timezone.utc)
            await session.commit()
    except Exception as exc:
        logger.error(
            "AI job audit finish failed | job_type=%s | key=%s | error_type=%s",
            spec.job_type,
            spec.idempotency_key,
            type(exc).__name__,
        )
