"""The batched readiness check agrees with the per-course one."""

import datetime as dt
import random

from src.models.db.interview import Interview
from src.models.db.practice_drill_completion import PracticeDrillCompletion
from src.models.db.readiness_rule import ReadinessRule
from src.models.db.summary_report import SummaryReport
from src.models.db.user import User
from src.services import readiness

from .test_fixed_question_rounds import db, pytestmark  # noqa: F401

UTC = dt.timezone.utc


async def test_ready_students_matches_assess_for_every_course(db):
    _, factory, _ = db
    rnd = random.Random(7)
    now = dt.datetime.now(UTC)
    async with factory() as s:
        s.add(ReadinessRule(course_id="web", window_days=10, min_mock_interviews=2, min_average_score=60, min_drills=1, require_improving=True, version=1))
        people = []
        for i in range(24):
            u = User(email=f"r{i}@example.com", name=f"R {i}", password_hash="x")
            s.add(u)
            await s.flush()
            people.append(u.id)
            for course in ("ds", "web"):
                for _ in range(rnd.randint(0, 7)):
                    at = now - dt.timedelta(days=rnd.randint(0, 20))
                    iv = Interview(user_id=u.id, track="Dev", course_id=course, interview_mode="practice", status="completed", created_at=at, completed_at=at)
                    s.add(iv)
                    await s.flush()
                    pct = rnd.randint(55, 98)
                    s.add(SummaryReport(interview_id=iv.id, report_json={"overallScoreSummary": {"overallPercentage": pct}}))
            for k in range(rnd.randint(0, 6)):
                s.add(PracticeDrillCompletion(user_id=u.id, module="pacing", level="easy", client_completion_id=f"{u.id}-{k}", evidence_kind="drill", evidence_units=1, completed_at=now - dt.timedelta(days=rnd.randint(0, 40))))
        await s.commit()
        by_course = {"ds": people[:16], "web": people[8:]}
        expected = set()
        for course, ids in by_course.items():
            rule = readiness.rule_view(await readiness.rule_for(s, course))
            found = await readiness.assess(s, course, ids, rule, now)
            expected |= {sid for sid, r in found.items() if r["status"] == "ready"}
        assert await readiness.ready_students(s, by_course, now) == expected
        assert expected, "the data should make some learners ready"
