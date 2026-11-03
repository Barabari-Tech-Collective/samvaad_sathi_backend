"""Seed safe, realistic demo data for the Learners workspace.

The script creates or updates a small set of demo-only learner accounts,
completed mock-interview evidence, and cohorts. It is deliberately idempotent:
re-running it updates the same records instead of multiplying rows.

It refuses non-local databases unless the operator explicitly confirms the
target. This makes it appropriate for local visual QA while preventing an
accidental staging/production write caused by an environment-file mistake.

    python ops/seed_learners_workspace_demo.py
    python ops/seed_learners_workspace_demo.py --course-id <staging-course-id> --i-know-what-im-doing

The second command is intentionally required for a shared database. Do not use
it on production. Demo accounts use the `demo.learner+` email namespace and
are never granted staff access.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import pathlib
import sys

import sqlalchemy as sa

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/backend"))

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
COURSE_ID = "demo-full-stack-2026"

LEARNERS = (
    ("Arma Sahar", "Vikram Government College", 86.0, 78.0),
    ("Aisha Khan", "ABC Institute", 43.0, None),
    ("Rahul Sharma", "City Tech", 91.0, 84.0),
    ("Priya Patel", "Vikram Government College", 74.0, 65.0),
    ("Marcus Vance", "Apex College", 31.0, None),
    ("Devin Brooks", "State Polytechnic", 68.0, 58.0),
)


def _email(index: int) -> str:
    return f"demo.learner+{index}@example.test"


def _is_local_target() -> bool:
    from src.config.manager import settings

    return settings.DB_POSTGRES_HOST in LOCAL_HOSTS


async def seed(course_id: str) -> None:
    from src.models.db.cohort import Cohort, CohortMember
    from src.models.db.interview import Interview
    from src.models.db.report import Report
    from src.models.db.user import User
    from src.repository.database import async_db
    from src.securities.hashing.password import pwd_generator

    session = async_db.get_session()
    now = dt.datetime.now(dt.timezone.utc)
    created = 0
    try:
        learners: list[User] = []
        for index, (name, college, latest_score, previous_score) in enumerate(LEARNERS, start=1):
            email = _email(index)
            learner = (
                await session.execute(sa.select(User).where(User.email == email))
            ).scalar_one_or_none()
            if learner is None:
                learner = User(
                    email=email,
                    name=name,
                    password_hash=pwd_generator.generate_hashed_password(
                        hash_salt="", new_password="demo-only-not-for-login"
                    ),
                    student_id=f"DEMO-LRN-{index:03d}",
                    central_identity_id=f"demo-learners-workspace-{index:03d}",
                    is_onboarded=True,
                    is_admin=False,
                    role=None,
                )
                session.add(learner)
                created += 1
            learner.name = name
            learner.university = college
            learner.degree = "B.Tech"
            learner.target_position = "Software Development"
            learner.enrolled_course_ids = [course_id]
            learner.enrollment_source = "demo_seed"
            learner.enrollment_observed_at = now
            learners.append(learner)

        await session.flush()

        # Create score-bearing completed mocks only when absent. The analytics
        # endpoints use these Report rows, so lists, readiness and the learner
        # dossier have representative states instead of fabricated client data.
        for index, learner in enumerate(learners, start=1):
            name, _college, latest_score, previous_score = LEARNERS[index - 1]
            existing = list(
                (
                    await session.execute(
                        sa.select(Interview)
                        .where(
                            Interview.user_id == learner.id,
                            Interview.course_id == course_id,
                            Interview.track == "Demo Full Stack",
                        )
                        .order_by(Interview.created_at)
                    )
                ).scalars()
            )
            target_scores = [score for score in (previous_score, latest_score) if score is not None]
            for offset, score in enumerate(target_scores[len(existing) :], start=len(existing)):
                completed_at = now - dt.timedelta(days=max(1, 14 - offset * 6 + index))
                interview = Interview(
                    user_id=learner.id,
                    track="Demo Full Stack",
                    course_id=course_id,
                    difficulty="medium",
                    status="completed",
                    interview_mode="practice",
                    created_at=completed_at,
                    completed_at=completed_at,
                    duration_seconds=900,
                )
                session.add(interview)
                await session.flush()
                session.add(
                    Report(
                        interview_id=interview.id,
                        overall_score=score,
                        summary={"source": "demo_learners_workspace_seed", "learner": name},
                        knowledge_competence={"score": score},
                        speech_structure_fluency={"score": max(0, score - 4)},
                    )
                )

        cohort_specs = (
            ("Cohort A · Full Stack Development", "Active demo batch with placement-ready learners.", learners[:3]),
            ("Cohort B · Full Stack Development", "Demo batch needing mentoring and mock interview follow-up.", learners[3:]),
        )
        for position, (name, description, members) in enumerate(cohort_specs, start=1):
            request_key = f"demo-learners-{course_id}-{position}"
            cohort = (
                await session.execute(
                    sa.select(Cohort).where(
                        Cohort.course_id == course_id,
                        Cohort.request_key == request_key,
                    )
                )
            ).scalar_one_or_none()
            if cohort is None:
                cohort = Cohort(
                    course_id=course_id,
                    request_key=request_key,
                    name=name,
                    description=description,
                )
                session.add(cohort)
                await session.flush()
            else:
                cohort.name, cohort.description = name, description
            for learner in members:
                present = (
                    await session.execute(
                        sa.select(CohortMember.id).where(
                            CohortMember.cohort_id == cohort.id,
                            CohortMember.student_id == learner.id,
                        )
                    )
                ).scalar_one_or_none()
                if present is None:
                    session.add(CohortMember(cohort_id=cohort.id, student_id=learner.id))

        await session.commit()
        print(f"Learners workspace demo seed complete: {len(learners)} learners, {created} new accounts, 2 cohorts.")
        print(f"Course ID: {course_id}")
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--i-know-what-im-doing",
        action="store_true",
        help="allow the configured non-local database target",
    )
    parser.add_argument(
        "--course-id",
        default=COURSE_ID,
        help="course identifier from the programme catalogue (default: %(default)s)",
    )
    args = parser.parse_args()
    if not args.i_know_what_im_doing and not _is_local_target():
        raise SystemExit(
            "Refusing to seed a non-local database. Use --i-know-what-im-doing only for an approved disposable staging target."
        )
    asyncio.run(seed(args.course_id))


if __name__ == "__main__":
    main()
