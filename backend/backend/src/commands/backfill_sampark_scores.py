"""Load resume scores into Sampark Saathi without sending WhatsApp messages (go-live backfill).

While Sampark was paused, scores computed here never reached it. This posts the latest score of
each listed student to Sampark with ``X-Backfill: true``, which stores the score and sends no
message. Staff then decide who gets a late message from Sampark's catch-up report.

Input is the list of student unique ids Sampark has no score for: the ``student_unique_id``
column of Sampark's ``scripts/golive/missing-scores.sql`` saved as a file (one id per line, or
that CSV). Run from ``backend/backend``::

    python -m src.commands.backfill_sampark_scores --ids missing.csv --dry-run
    python -m src.commands.backfill_sampark_scores --ids missing.csv

Prints counts only: no names, emails or phone numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from src.services.barabari_integration import PermanentCallbackError, submit_resume_score_to_barabari


@dataclass
class LatestScore:
    student_id: str
    score: int
    target_role: str | None


@dataclass
class Report:
    requested: int = 0
    no_analysis: int = 0
    to_send: int = 0
    sent: int = 0
    refused: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)

    def lines(self, dry_run: bool) -> list[str]:
        out = [
            f"students listed:            {self.requested}",
            f"no resume analysis here:    {self.no_analysis}",
            f"to send (latest score):     {self.to_send}",
        ]
        if not dry_run:
            out += [
                f"stored in Sampark:          {self.sent}",
                f"refused by Sampark (4xx):   {self.refused}",
                f"failed (retry later):       {self.failed}",
            ]
        return out


def read_ids(path: Path) -> list[str]:
    """Reads ids from a plain list or a CSV with a student_unique_id column. Keeps order, drops duplicates."""
    text = path.read_text(encoding="utf-8").splitlines()
    rows = list(csv.reader(text))
    ids: list[str] = []
    if rows and "student_unique_id" in [c.strip() for c in rows[0]]:
        col = [c.strip() for c in rows[0]].index("student_unique_id")
        ids = [r[col].strip() for r in rows[1:] if len(r) > col]
    else:
        ids = [r[0].strip() for r in rows if r]
    seen: set[str] = set()
    return [i for i in ids if i and not (i in seen or seen.add(i))]


def latest_scores(rows: Iterable[tuple[int, str, dict | None, str | None]]) -> dict[str, LatestScore]:
    """rows: (analysis row id, student id, analysis_result, target_role). The highest row id wins."""
    best: dict[str, tuple[int, LatestScore]] = {}
    for row_id, student_id, result, target_role in rows:
        score = (result or {}).get("atsScore")
        if not student_id or not isinstance(score, (int, float)):
            continue
        if student_id not in best or row_id > best[student_id][0]:
            best[student_id] = (row_id, LatestScore(student_id, int(round(score)), target_role))
    return {k: v for k, (_, v) in best.items()}


Post = Callable[[LatestScore, str], Awaitable[None]]


async def run(
    ids: list[str],
    scores: dict[str, LatestScore],
    post: Post,
    *,
    dry_run: bool,
    per_second: float = 5.0,
) -> Report:
    report = Report(requested=len(ids))
    todo = [scores[i] for i in ids if i in scores]
    report.no_analysis = len(ids) - len(todo)
    report.to_send = len(todo)
    if dry_run:
        return report
    for item in todo:
        try:
            await post(item, f"backfill-{uuid.uuid4()}")
            report.sent += 1
        except PermanentCallbackError:
            report.refused += 1
        except Exception:  # noqa: BLE001 - counted and reported; the run continues
            report.failed += 1
            report.failures.append(item.student_id)
        if per_second > 0:
            await asyncio.sleep(1 / per_second)
    return report


async def _fetch_rows(ids: list[str]) -> list[tuple[int, str, dict | None, str | None]]:
    from sqlalchemy import select

    from src.models.db.ai_resume_analysis import AIResumeAnalysis
    from src.models.db.user import User
    from src.repository.database import async_db

    async with async_db.async_session_factory() as session:
        stmt = (
            select(AIResumeAnalysis.id, User.student_id, AIResumeAnalysis.analysis_result, AIResumeAnalysis.target_role)
            .join(User, User.id == AIResumeAnalysis.user_id)
            .where(User.student_id.in_(ids))
        )
        return [tuple(r) for r in (await session.execute(stmt)).all()]


async def _post(item: LatestScore, request_id: str) -> None:
    await submit_resume_score_to_barabari(
        student_id=item.student_id,
        resume_score=item.score,
        request_id=request_id,
        target_role=item.target_role,
        backfill=True,
    )


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ids", type=Path, required=True, help="file of student unique ids (missing-scores.sql output)")
    parser.add_argument("--dry-run", action="store_true", help="print counts only, send nothing")
    parser.add_argument("--per-second", type=float, default=5.0)
    args = parser.parse_args(argv)

    ids = read_ids(args.ids)
    scores = latest_scores(await _fetch_rows(ids)) if ids else {}
    report = await run(ids, scores, _post, dry_run=args.dry_run, per_second=args.per_second)
    print("DRY RUN - nothing sent" if args.dry_run else "Backfill finished")
    print("\n".join(report.lines(args.dry_run)))
    if report.failures:
        Path("backfill-failed-ids.txt").write_text("\n".join(report.failures) + "\n", encoding="utf-8")
        print("failed ids written to backfill-failed-ids.txt; rerun with --ids backfill-failed-ids.txt")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
