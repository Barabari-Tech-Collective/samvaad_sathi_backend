"""Dummy learners for the staging Learners area. Refuses every database except staging.

Run on the staging server, from backend/backend:
    python scripts/seed_learner_hub_demo.py --seed
    python scripts/seed_learner_hub_demo.py --remove

The production host also runs with ENVIRONMENT=DEV, so the environment name proves nothing.
The check is the database itself: only staging's exact host and database name are accepted,
compared by hash. Dummy learners use .invalid addresses, which can never receive email.
"""

import argparse
import asyncio
import datetime as dt
import hashlib
import os
import random
import sys
import uuid

sys.path.insert(0, os.getcwd())

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

STAGING_DATABASE = "90e4ddde0ec04cb461be052445686f374e1fa118d3e0f9699501b9c42a60fbc1"
DOMAIN = "demo.samvaad.invalid"
PLACES = [
    ("Ranchi College", "demo-ds"), ("Ranchi College", "demo-web"), ("ranchi clg", "demo-ds"),
    ("Gumla College", "demo-ds"), ("Gumla College", "demo-web"), ("Hazaribagh College", "demo-ds"),
    ("Hazaribagh College", "demo-soft"), (None, "demo-ds"),
]
COURSES = {"demo-ds": "Data Science (demo)", "demo-web": "Web Dev (demo)", "demo-soft": "Soft Skills (demo)"}
TEMPLATE_MARK = " (demo)"
DEMO_TEAM = "Demo interview panel"
FIRST = ["Priya", "Aman", "Ravi", "Sara", "Neha", "Vikas", "Anjali", "Rahul", "Pooja", "Suman"]
LAST = ["Tirkey", "Gupta", "Toppo", "Minz", "Kujur", "Oraon", "Singh", "Munda"]


def database_url():
    from src.config.manager import settings

    if os.environ.get("DEMO_DATABASE_URL"):
        return sa.engine.make_url(os.environ["DEMO_DATABASE_URL"])
    return sa.URL.create(
        "postgresql+asyncpg", username=settings.DB_POSTGRES_USERNAME, password=settings.DB_POSTGRES_PASSWORD,
        host=settings.DB_POSTGRES_HOST, port=settings.DB_POSTGRES_PORT, database=settings.DB_POSTGRES_NAME,
    )


def refuse_unless_staging(url) -> None:
    local = url.host in ("127.0.0.1", "localhost") and (url.database or "").startswith("ss_")
    fingerprint = hashlib.sha256(f"{url.host}/{url.database}".encode()).hexdigest()
    if not local and fingerprint != STAGING_DATABASE:
        raise SystemExit("REFUSED: this is not the staging database. Dummy data is never written elsewhere.")


async def remove(factory) -> int:
    from src.models.db.human_interview import HumanInterview
    from src.models.db.integrity_case import IntegrityCase
    from src.models.db.job_profile import JobProfile
    from src.models.db.team import Team
    from src.models.db.user import User

    demo_users = sa.select(User.id).where(User.email.like(f"%@{DOMAIN}"))
    async with factory() as s:
        # These point at people or templates in a way that blocks a simple delete.
        await s.execute(sa.delete(IntegrityCase).where(IntegrityCase.student_id.in_(demo_users)))
        await s.execute(sa.delete(HumanInterview).where(HumanInterview.student_id.in_(demo_users)))
        result = await s.execute(sa.delete(User).where(User.email.like(f"%@{DOMAIN}")))
        # Published templates are locked by database triggers. The triggers are switched off
        # only inside this transaction, so nothing else ever sees them off.
        for trigger in ("job_profile ss_guard_publication", "job_profile_question ss_guard_published_question"):
            table, name = trigger.split()
            await s.execute(sa.text(f"ALTER TABLE {table} DISABLE TRIGGER {name}"))
        demo_templates = sa.select(JobProfile.id).where(JobProfile.job_name.like(f"%{TEMPLATE_MARK}"))
        await s.execute(sa.update(JobProfile).where(JobProfile.parent_profile_id.in_(demo_templates)).values(parent_profile_id=None))
        await s.execute(sa.delete(JobProfile).where(JobProfile.job_name.like(f"%{TEMPLATE_MARK}")))
        for trigger in ("job_profile ss_guard_publication", "job_profile_question ss_guard_published_question"):
            table, name = trigger.split()
            await s.execute(sa.text(f"ALTER TABLE {table} ENABLE TRIGGER {name}"))
        await s.execute(sa.delete(Team).where(Team.name == DEMO_TEAM))
        await s.commit()
    return result.rowcount


async def seed(factory, count: int) -> int:
    import src.models.db  # noqa: F401
    from src.models.db.interview import Interview
    from src.models.db.practice_drill_completion import PracticeDrillCompletion
    from src.models.db.student_placement import StudentPlacement
    from src.models.db.summary_report import SummaryReport
    from src.models.db.user import User
    from src.services.sampark_enrollment_sync import SELF_SOURCE

    now = dt.datetime.now(dt.timezone.utc)
    r = random.Random(11)
    await remove(factory)
    async with factory() as s:
        users = []
        for i in range(count):
            college, course = PLACES[i % len(PLACES)]
            users.append(
                User(
                    email=f"learner{i}@{DOMAIN}", name=f"Demo {FIRST[i % 10]} {LAST[i % 8]} {i}",
                    password_hash="demo-account-cannot-sign-in", university=college, enrolled_course_ids=[course],
                    enrollment_source=SELF_SOURCE, enrollment_observed_at=now,
                    created_at=now - dt.timedelta(days=30 + i % 40),
                )
            )
        s.add_all(users)
        await s.flush()
        for i, user in enumerate(users):
            _, course = PLACES[i % len(PLACES)]
            if i % 5:
                s.add(
                    StudentPlacement(
                        student_id=user.id, batch_key=f"{course}-{i % 3}", batch_name=f"Batch {'ABC'[i % 3]}",
                        course_id=course, course_name=COURSES[course],
                    )
                )
            kind = i % 10
            if kind == 0:
                continue  # never started
            last = {1: 0, 2: 2, 3: 12, 4: 20, 5: 3, 6: 4, 7: 1, 8: 2, 9: 5}[kind]
            score = {5: 45, 6: 48, 7: 82, 8: 76}.get(kind, r.randint(62, 74))
            for j in range(4 if kind in (7, 8) else 2):
                at = now - dt.timedelta(days=last + j)
                interview = Interview(
                    user_id=user.id, track="Demo", course_id=course, status="completed",
                    interview_mode="assessment" if j == 0 else "practice", created_at=at, completed_at=at,
                )
                s.add(interview)
                await s.flush()
                part = {"percentage": score}
                s.add(
                    SummaryReport(
                        interview_id=interview.id,
                        report_json={"overallScoreSummary": {"overallPercentage": score, "knowledgeCompetence": part, "speechStructure": part}},
                    )
                )
            if kind in (7, 8):
                for n in range(5):
                    s.add(
                        PracticeDrillCompletion(
                            user_id=user.id, module="warm_up", level="easy", client_completion_id=f"demo-{i}-{n}",
                            evidence_kind="completed", evidence_units=1, completed_at=now - dt.timedelta(days=n + 1),
                        )
                    )
        await s.commit()
    await seed_interviews(factory, now)
    return count


def demo_questions(profile_id, skills, per_level):
    from src.models.db.job_profile_question import JobProfileQuestion

    prompts = (
        ("In your own words, what does {} mean?", "Explain {} in simple words.", "Why is {} important in this job?"),
        ("Tell me about a project that involved {}.", "How do you practise {}?", "Walk me through how you would start with {}."),
        ("{} suddenly fails. What do you check first?", "How would you make {} faster?", "What would you test before releasing work on {}?"),
        ("Design a plan for {} across a large team.", "What trade-offs would you weigh for {}?", "How would you measure whether {} is working?"),
    )
    rows = []
    for level, counts in zip((1, 2, 3, 4), per_level):
        made = 0
        for prompt in prompts[level - 1]:
            for skill in skills:
                if made < counts:
                    rows.append(
                        JobProfileQuestion(
                            job_profile_id=profile_id, question_text=(lambda q: q[0].upper() + q[1:])(prompt.format(skill)), level=level,
                            difficulty=("easy", "medium", "hard", "expert")[level - 1], question_type="theoretical",
                            is_ai_generated=False, expected_answer="Looks for a clear example and what the learner did.",
                        )
                    )
                    made += 1
    return rows


async def seed_interviews(factory, now) -> None:
    from src.models.db.assessment_assignment import AssessmentAssignment
    from src.models.db.feedback_issue import FeedbackIssue
    from src.models.db.human_interview import HumanInterview
    from src.models.db.human_scorecard import HumanScorecard
    from src.models.db.integrity_case import IntegrityCase
    from src.models.db.interview import Interview
    from src.models.db.interview_question import InterviewQuestion
    from src.models.db.job_profile import JobProfile
    from src.models.db.notification import NotificationDelivery
    from src.models.db.question_attempt import QuestionAttempt
    from src.models.db.summary_report import SummaryReport
    from src.models.db.team import Team
    from src.models.db.team_member import TeamMember
    from src.models.db.user import User

    day = dt.timedelta(days=1)
    async with factory() as s:
        host = User(email=f"interviewer@{DOMAIN}", name="Demo Interviewer", password_hash="demo-account-cannot-sign-in")
        s.add(host)
        team = Team(name=DEMO_TEAM)
        s.add(team)
        await s.flush()
        s.add(TeamMember(team_id=team.id, user_id=host.id, access_role="Team Member", purposes=[]))

        # Questions cannot be added to a published template, so every template starts as a
        # draft and is published afterwards, the same order the app uses.
        to_publish = []

        def template(name, course, skills, status, mode="assessment", per=5, parent=None, age=None, comment=None, rule="immediate"):
            profile = JobProfile(
                job_name=f"{name}{TEMPLATE_MARK}", job_description=f"Demo {name} role. The learner explains their own work clearly.",
                company_name=None, category="IT", experience_level="Fresher", employment_type="Full-time", skills=skills,
                course_ids=[course], status="draft" if status == "published" else status, interview_mode=mode, questions_per_interview=per,
                authoring_mode="manual", parent_profile_id=parent, result_release=rule, admin_comment=comment,
            )
            if status == "published":
                to_publish.append((profile, now - (age or 1) * day))
            return profile

        analyst_v1 = template("Data Analyst", "demo-ds", ["SQL joins", "data cleaning", "dashboards"], "published", age=40, rule="review")
        s.add(analyst_v1)
        await s.flush()
        live = {
            "demo-ds": template("Data Analyst", "demo-ds", ["SQL joins", "data cleaning", "dashboards"], "published", parent=analyst_v1.id, age=8, rule="review"),
            "demo-web": template("Frontend Developer", "demo-web", ["React state", "CSS layout", "accessibility"], "published", age=20),
            "demo-soft": template("Communication Round", "demo-soft", ["listening", "clear writing", "feedback"], "published", age=15),
        }
        sde = template("SDE Intern", "demo-ds", ["arrays", "debugging", "APIs"], "published", age=12)
        mocks = {
            "demo-ds": template("Project defence", "demo-ds", ["your final project", "your choices", "your results"], "published", "practice", age=10),
            "demo-web": template("Python basics", "demo-web", ["lists", "functions", "errors"], "published", "practice", age=10),
            "demo-soft": template("HR round", "demo-soft", ["your strengths", "teamwork", "your goals"], "published", "practice", age=10),
        }
        extras = [
            (template("Business Analyst", "demo-ds", ["requirements", "stakeholders", "reports"], "draft"), (6, 6, 6, 3)),
            (template("UI Designer", "demo-web", ["wireframes", "user research", "design systems"], "under_review"), (6, 6, 6, 6)),
            (template("Customer Success", "demo-soft", ["difficult calls", "renewals", "handover"], "changes_requested", comment="Medium questions repeat the Easy ones."), (6, 6, 6, 6)),
        ]
        everything = list(live.values()) + [sde] + list(mocks.values()) + [e[0] for e in extras]
        s.add_all(everything)
        await s.flush()
        s.add_all(demo_questions(analyst_v1.id, ["SQL joins", "data cleaning", "dashboards"], (6, 6, 6, 6)))
        for profile in list(live.values()) + [sde] + list(mocks.values()):
            s.add_all(demo_questions(profile.id, profile.skills, (6, 6, 6, 6)))
        for profile, counts in extras:
            s.add_all(demo_questions(profile.id, profile.skills, counts))
        await s.flush()
        for profile, published_at in to_publish:
            # Same published copy the app keeps: the template and its questions as they were.
            await s.execute(
                sa.text(
                    """UPDATE job_profile p SET status='published', published_at=:at,
                    publication_snapshot=jsonb_build_object('version',1,'publishedAt',CAST(:iso AS text),
                    'profile',to_jsonb(p)-'publication_snapshot'-'status'-'published_at',
                    'questions',COALESCE((SELECT jsonb_agg(to_jsonb(q) ORDER BY q.id) FROM job_profile_question q WHERE q.job_profile_id=p.id),'[]'::jsonb))
                    WHERE p.id=:id"""
                ),
                {"id": profile.id, "at": published_at, "iso": published_at.isoformat()},
            )

        rows = (
            await s.execute(
                sa.select(Interview, User)
                .join(User, User.id == Interview.user_id)
                .where(User.email.like(f"learner%@{DOMAIN}"))
                .order_by(Interview.id)
            )
        ).all()
        difficulty = ("easy", "medium", "hard")
        for n, (interview, user) in enumerate(rows):
            course = interview.course_id
            interview.difficulty = difficulty[n % 3]
            interview.duration_seconds = 540 + n % 25 * 60
            if interview.interview_mode == "practice":
                interview.job_profile_id = mocks[course].id
                continue
            interview.job_profile_id = sde.id if course == "demo-ds" and n % 3 == 0 else live[course].id
            if n % 5 == 0:
                interview.result_release = "review"
            if n % 17 == 0:
                await s.execute(sa.delete(SummaryReport).where(SummaryReport.interview_id == interview.id))
            if n % 7 == 0 and n < 2100:
                for order, text in enumerate(("Tell me about a project you are proud of.", "How would you find duplicate rows?", "Why this role?"), 1):
                    question = InterviewQuestion(interview_id=interview.id, text=text, category="tech" if order == 2 else "behavioral", order=order, status="answered")
                    s.add(question)
                    await s.flush()
                    s.add(
                        QuestionAttempt(
                            interview_id=interview.id, question_id=question.id, question_text=text, answer_kind="audio",
                            answer_saved_at=interview.completed_at, attempt_state="answered", transcription_status="transcribed",
                            transcription={"text": "This is a demo answer. I built a small tool and tested it with my team."},
                            feedback="Clear example. Add the result you achieved at the end.",
                        )
                    )

        assessed = [(i, u) for i, u in rows if i.interview_mode == "assessment"]
        scored_ids = set((await s.execute(sa.select(SummaryReport.interview_id))).scalars())
        for n, (interview, user) in enumerate([x for x in assessed if x[0].id in scored_ids][:8]):
            case_at = now - (n + 1) * day
            s.add(
                IntegrityCase(
                    interview_id=interview.id, student_id=user.id, course_id=interview.course_id, college=(user.university or "").lower() or None,
                    opened_by=host.id, request_key=str(uuid.uuid4()), request_hash="demo", state="appealed" if n % 4 == 3 else "open",
                    outcome="concern" if n % 4 == 3 else None,
                    history=[{"kind": "opened", "reason": "The interview tab was left several times. Needs a fair look.", "source": "technical_context", "at": case_at.isoformat(), "actor_id": host.id, "request_key": str(uuid.uuid4()), "request_hash": "demo"}],
                    created_at=case_at,
                )
            )
        kinds = ("transcription", "feedback", "language", "other")
        for n, (interview, user) in enumerate(assessed[8:13]):
            s.add(FeedbackIssue(reporter_id=user.id, interview_id=interview.id, kind=kinds[n % 4], explanation="Demo report: this does not look right to me.", status="open", request_key=str(uuid.uuid4())))

        people = (await s.execute(sa.select(User).where(User.email.like(f"learner%@{DOMAIN}")).order_by(User.id))).scalars().all()
        meet = "https://meet.google.com/abc-defg-hij"
        # (days from today, status, scorecard status, released)
        plans = [(-3, "scheduled", None, False)] * 3 + [(0, "scheduled", None, False)] * 4 + [(3, "scheduled", None, False)] * 8
        plans += [(-4, "completed", None, False)] * 4 + [(-5, "completed", "draft", False)] * 2 + [(-6, "completed", "submitted", False)] * 4
        plans += [(-8, "completed", "approved", False)] * 3 + [(-10, "completed", "approved", True)] * 5 + [(-7, "no_show", None, False)] * 2 + [(-9, "technical_issue", None, False)]
        hours = (5, 7, 9, 12)
        for n, (offset, status, card, released) in enumerate(plans):
            person = people[(n * 3 + 1) % len(people)]
            course = (person.enrolled_course_ids or ["demo-ds"])[0]
            # Today's interviews stay ahead of "now", and the first opens for joining straight away.
            start = (
                now + dt.timedelta(minutes=10 + 45 * (n % 4))
                if offset == 0
                else (now + offset * day).replace(hour=hours[n % 4], minute=0, second=0, microsecond=0)
            )
            row = HumanInterview(
                course_id=course, student_id=person.id, host_id=host.id, interviewer_id=host.id, title=f"Technical Round {n % 2 + 1}", role_name="Data Analyst" if n % 2 else None,
                meet_url=meet, starts_at=start, ends_at=start + dt.timedelta(minutes=30), request_key=str(uuid.uuid4()), request_hash="demo",
                status=status, version=1, instructions="Focus on the final-year project and one coding problem.",
                result="next_round" if released else None, feedback="Good structure. Practise explaining trade-offs." if released else None,
                result_released_at=start + 2 * day if released else None, closed_at=start + dt.timedelta(hours=1) if status != "scheduled" else None,
                closed_by=host.id if status != "scheduled" else None, close_reason="Demo outcome recorded." if status != "scheduled" else None,
            )
            s.add(row)
            await s.flush()
            if card:
                s.add(HumanScorecard(appointment_id=row.id, status=card, version=1, suggested_result="next_round", student_comments="Good structure. Practise explaining trade-offs.", internal_notes="Demo private note.", criteria=[{"name": "Problem solving", "score": 4, "evidence": "Reasoned aloud."}, {"name": "Communication", "score": 3, "evidence": "Clear but brief."}]))
            if status == "scheduled" and offset > 0 and n % 4 == 0:
                s.add(NotificationDelivery(recipient_id=person.id, event="scheduled", related_id=row.id, status="failed", last_error="Demo failure"))

        windows = [("demo-ds", live["demo-ds"], -1 * day, 2 * day, 40), ("demo-web", live["demo-web"], -3 * day, 0.2 * day, 25), ("demo-soft", live["demo-soft"], 4 * day, 11 * day, 20)]
        for course, profile, opens, closes, size in windows:
            members = [p for p in people if course in (p.enrolled_course_ids or [])][:size]
            for m, person in enumerate(members):
                s.add(
                    AssessmentAssignment(
                        student_id=person.id, assigned_by=host.id, course_id=course, job_profile_id=profile.id, pinned_revision=0,
                        difficulty="medium", opens_at=(now + opens).replace(microsecond=0), closes_at=(now + closes).replace(microsecond=0),
                        request_key=str(uuid.uuid4()), status="started" if m % 6 == 0 and opens < dt.timedelta(0) else "assigned", version=1,
                        instructions="Finish it in one sitting, in a quiet room.",
                    )
                )
        await s.commit()


async def main() -> None:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--seed", action="store_true")
    action.add_argument("--remove", action="store_true")
    parser.add_argument("--count", type=int, default=400)
    args = parser.parse_args()
    url = database_url()
    refuse_unless_staging(url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        print("removed" if args.remove else "seeded", await (remove(factory) if args.remove else seed(factory, args.count)))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
