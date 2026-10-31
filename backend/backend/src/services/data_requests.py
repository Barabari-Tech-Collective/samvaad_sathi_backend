"""Student data export and deletion (architecture pack F32).

Deletion removes only what is the learner's own practice material and says so; formal
evidence (assessment records, interviews, selections, accommodations, integrity cases, audit)
is reported as retained with a reason. The result is verified by recounting afterwards."""

import sqlalchemy as sa

from src.models.db.user_resume import UserResume
from src.services import s3_service

# (category, table, owner column, extra condition)
DELETABLE = [
    ("Pronunciation practice", "pronunciation_practice", "user_id", None),
    ("Answer-structure practice", "structure_practice", "user_id", None),
    ("Pacing practice", "pacing_practice_session", "user_id", None),
    ("Drill completions", "practice_drill_completion", "user_id", None),
    ("Mock interviews (practice mode)", "interview", "user_id", "interview_mode = 'practice'"),
    ("Confirmed profile context", "learner_context", "user_id", None),
    ("Reported issues", "feedback_issue", "reporter_id", None),
    ("Notifications", "notification", "recipient_id", None),
]
RETAINED = [
    ("Assessment interviews and their scores", "Formal evidence is kept for selection and appeal."),
    ("Human interviews and scorecards", "Part of the formal selection record."),
    ("Opportunity selections", "Part of the formal selection record."),
    ("Accommodation requests", "Kept so an earlier assessment can be explained."),
    ("Integrity cases", "Kept while an appeal or review may still happen."),
    ("Audit history", "Required to show who accessed what."),
]
EXPORTED = [
    ("profile", "SELECT id, name, email, created_at FROM \"user\" WHERE id = :u"),
    ("interviews", "SELECT id, status, interview_mode, course_id, created_at FROM interview WHERE user_id = :u"),
    ("pronunciation_practice", "SELECT * FROM pronunciation_practice WHERE user_id = :u"),
    ("structure_practice", "SELECT * FROM structure_practice WHERE user_id = :u"),
    ("pacing_practice", "SELECT * FROM pacing_practice_session WHERE user_id = :u"),
    ("profile_context", "SELECT * FROM learner_context WHERE user_id = :u"),
    ("accommodation_requests", "SELECT id, status, conditions, created_at FROM accommodation_request WHERE student_id = :u"),
    ("notifications", "SELECT kind, title, created_at FROM notification WHERE recipient_id = :u"),
]


def _where(owner_column, extra):
    return f"{owner_column} = :u" + (f" AND {extra}" if extra else "")


async def count(session, table, owner_column, extra, student_id) -> int:
    return (
        await session.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE {_where(owner_column, extra)}"),  # noqa: S608
            {"u": student_id},
        )
    ).scalar_one()


async def build_export(session, student_id: int) -> dict:
    data = {}
    for key, sql in EXPORTED:
        rows = (await session.execute(sa.text(sql), {"u": student_id})).mappings().all()
        data[key] = [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in rows]
    resumes = (
        await session.execute(sa.select(UserResume.filename, UserResume.created_at).where(UserResume.user_id == student_id))
    ).all()
    data["resumes"] = [{"filename": f, "uploaded_at": c.isoformat()} for f, c in resumes]
    data["not_included"] = [
        {"category": c, "reason": "Held as formal evidence; ask the programme team for a copy."} for c, _ in RETAINED
    ]
    return data


async def execute_deletion(session, student_id: int) -> dict:
    """Delete, verify by recounting, and report honestly. Caller commits."""
    deleted, pending = [], []
    keys = (await session.execute(sa.select(UserResume.s3_key).where(UserResume.user_id == student_id))).scalars().all()
    stored = await s3_service.delete_objects_from_s3(list(keys))
    if stored["errors"]:
        pending.append({"category": "Stored resume files", "reason": "Some files could not be removed yet and need a retry."})
    else:
        # Only drop the resume rows once their files are confirmed gone.
        n = (await session.execute(sa.delete(UserResume).where(UserResume.user_id == student_id))).rowcount
        deleted.append({"category": "Resumes", "count": n})
    for category, table, column, extra in DELETABLE:
        before = await count(session, table, column, extra, student_id)
        await session.execute(sa.text(f"DELETE FROM {table} WHERE {_where(column, extra)}"), {"u": student_id})  # noqa: S608
        left = await count(session, table, column, extra, student_id)
        if left:
            pending.append({"category": category, "reason": f"{left} records remain."})
        deleted.append({"category": category, "count": before - left})
    pending.append(
        {"category": "Answer recordings", "reason": "Recordings are removed with their interviews on the next storage sweep."}
    )
    return {
        "deleted": deleted,
        "retained": [{"category": c, "reason": r} for c, r in RETAINED],
        "pending": pending,
    }
