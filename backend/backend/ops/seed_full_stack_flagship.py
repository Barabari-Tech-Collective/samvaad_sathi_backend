"""Seed the Full Stack Developer flagship question bank.

The first domain-expert-authored content set for the curated interview
system: 20 questions across all four levels (5 each, matching
MINIMUM_QUESTIONS_PER_LEVEL), each with an expected-answer rubric rather than
a model answer - these are open-ended engineering questions, not fact recall,
so "expected answer" here means what a strong response covers, not a string
to match against.

Defaults to interview_mode="practice": this is meant as an open resource any
student can sit and get scored on, the scouting signal a facilitator later
uses to invite strong performers to a real Assessment - not a gated,
company-specific sitting itself. Pass --mode assessment to seed it as one
instead.

Refuses to run against anything but a database on this machine unless you
pass --i-know-what-im-doing, mirroring ops/dev_login_token.py: this writes
real rows, and a seed script that happily pointed at whatever
DB_POSTGRES_HOST happened to be configured would be one `.env` mismatch away
from writing into a shared database by accident.

    python ops/seed_full_stack_flagship.py                       # local, practice mode
    python ops/seed_full_stack_flagship.py --mode assessment      # local, assessment mode
    python ops/seed_full_stack_flagship.py --i-know-what-im-doing # against whatever DB_POSTGRES_HOST is set to
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/backend"))

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

JOB_NAME = "Full Stack Developer"
JOB_DESCRIPTION = (
    "A full stack engineering role spanning frontend, backend, data, and the "
    "production judgment to ship and operate both. This question bank is the "
    "flagship content set for the curated interview system - written to the "
    "same bar expected of a senior engineer interviewing a candidate, not "
    "generated or templated."
)
SKILLS = [
    "javascript", "typescript", "react", "node", "rest-apis", "sql",
    "system-design", "debugging", "security",
]

# level -> list of (question_text, question_type, keywords, concepts_covered, expected_answer)
QUESTION_BANK: dict[int, list[tuple[str, str, list[str], list[str], str]]] = {
    # Level 1 - Foundational. A bootcamp graduate or final-year student should
    # be able to answer these from first principles, without having shipped
    # anything to production yet.
    1: [
        (
            "Explain closures in JavaScript with a concrete example, and describe "
            "one real bug a misunderstood closure can cause.",
            "theoretical",
            ["closures", "scope", "javascript"],
            ["lexical scope", "variable capture"],
            "Defines a closure as a function retaining access to its enclosing "
            "scope's variables after that scope has returned. A strong answer "
            "gives an example (a counter factory, or a loop capturing `var` by "
            "reference) and names the classic bug: a `var` loop variable shared "
            "across all callbacks firing with the same final value, fixed with "
            "`let` or an IIFE.",
        ),
        (
            "What is the difference between `==` and `===` in JavaScript? Walk "
            "through one case where the difference would surprise someone.",
            "theoretical",
            ["equality", "type-coercion", "javascript"],
            ["type coercion", "strict equality"],
            "`==` coerces operands to a common type before comparing; `===` does "
            "not. A strong answer gives a coercion surprise - `0 == false`, "
            "`'' == 0`, or `null == undefined` but `null === undefined` is "
            "false - and states the practical rule: default to `===` and coerce "
            "explicitly when you mean to.",
        ),
        (
            "What is the difference between props and state in React, and who is "
            "allowed to change each?",
            "theoretical",
            ["react", "state", "props"],
            ["component model", "unidirectional data flow"],
            "Props are read-only data passed down from a parent; a component "
            "never mutates its own props. State is local, mutable data a "
            "component owns and updates via a setter, triggering a re-render. A "
            "strong answer connects this to one-way data flow: state can be "
            "passed down as a prop to a child, but the child cannot change it "
            "directly - it calls a function the parent passed down instead.",
        ),
        (
            "Explain the difference between an INNER JOIN and a LEFT JOIN, with "
            "an example of when each produces a different result.",
            "theoretical",
            ["sql", "joins", "databases"],
            ["relational joins", "null handling"],
            "INNER JOIN returns only rows with a match in both tables; LEFT "
            "JOIN returns every row from the left table, with NULLs for columns "
            "from the right table where no match exists. A strong answer gives "
            "an example: listing all customers including those with zero "
            "orders requires a LEFT JOIN from customers to orders - an INNER "
            "JOIN would silently drop them.",
        ),
        (
            "What is the difference between `git merge` and `git rebase`? When "
            "would you choose one over the other?",
            "theoretical",
            ["git", "version-control"],
            ["commit history", "merge strategies"],
            "Merge creates a new commit joining two histories, preserving both "
            "branches' commits as they happened. Rebase replays one branch's "
            "commits onto another, producing a linear history but rewriting "
            "commit hashes. A strong answer notes the practical rule: rebase "
            "local, unpushed work to keep history clean; merge (never rebase) "
            "a branch others have already pulled from.",
        ),
    ],
    # Level 2 - Project & Resume Based. Application of fundamentals to
    # decisions the candidate has actually made on a real project.
    2: [
        (
            "Describe a bug in a React application that took you a while to "
            "track down. What made it hard to find, and how did you eventually "
            "diagnose it?",
            "scenario",
            ["debugging", "react", "problem-solving"],
            ["debugging methodology", "root cause analysis"],
            "Look for a specific bug (not a generic description), a clear "
            "account of what made it non-obvious - a stale closure in a "
            "`useEffect`, a missing dependency, a race between two async "
            "updates - and a real diagnostic process: reading render logs, "
            "bisecting with React DevTools, or writing a minimal repro. Vague "
            "'I just found it eventually' answers are a red flag.",
        ),
        (
            "Design the REST endpoints for a basic todo list API: creating, "
            "listing, completing, and deleting a task. What status codes and "
            "request/response shapes would you use?",
            "practical",
            ["rest-api", "http", "api-design"],
            ["resource modeling", "http semantics"],
            "Expect something close to: POST /tasks (201 + created resource), "
            "GET /tasks (200 + array, ideally paginated), PATCH /tasks/{id} to "
            "mark complete (200 + updated resource, not a dedicated PUT that "
            "replaces the whole object for a one-field change), DELETE "
            "/tasks/{id} (204). A strong answer gets the verbs and status codes "
            "right and can explain why PATCH over PUT for a partial update.",
        ),
        (
            "In a project you've built, how did you protect against SQL "
            "injection and XSS? Be specific about the mechanism, not just the "
            "name of the attack.",
            "practical",
            ["security", "sql-injection", "xss"],
            ["parameterized queries", "output encoding"],
            "For SQL injection: parameterized queries or an ORM that binds "
            "parameters rather than string-concatenating SQL - the candidate "
            "should be able to say *why* concatenation is unsafe, not just that "
            "it is. For XSS: that a framework like React escapes interpolated "
            "values by default, and naming the specific unsafe path that "
            "bypasses it (`dangerouslySetInnerHTML`, `innerHTML` assignment) if "
            "they've ever had to use one and how they sanitized it.",
        ),
        (
            "How would you structure state in a medium-sized React app - props "
            "drilling, context, or an external store? Walk through how you'd "
            "decide.",
            "practical",
            ["react", "state-management", "architecture"],
            ["state colocation", "context api", "state libraries"],
            "A strong answer resists a one-size-fits-all rule: state should "
            "live as close to where it's used as possible; prop drilling is "
            "fine for a couple of levels; context suits state that's genuinely "
            "global but changes rarely (theme, auth); an external store (Redux, "
            "Zustand) earns its complexity when state is large, frequently "
            "updated, and needed in many unrelated places. Should mention "
            "context's main cost: every consumer re-renders on any change.",
        ),
        (
            "You chose either SQL or NoSQL for a project. What drove that "
            "decision, and what would have made you choose the other?",
            "practical",
            ["databases", "sql", "nosql", "architecture"],
            ["data modeling", "consistency trade-offs"],
            "Look for a decision grounded in the project's actual access "
            "patterns and consistency needs, not a generic preference. "
            "Relational data with multi-table transactions and the need for "
            "strong consistency points to SQL; a schema that varies per "
            "document, very high write volume, or a need to scale horizontally "
            "without sharding effort points to NoSQL. A strong answer can name "
            "a concrete feature of their project that tipped the decision.",
        ),
    ],
    # Level 3 - Production & Scenario Based. Debugging and judgment under the
    # kind of pressure a real incident creates.
    3: [
        (
            "After a deploy, your API's p95 response time jumps from 200ms to "
            "2 seconds. Walk through how you'd investigate, in order.",
            "scenario",
            ["debugging", "performance", "production-incidents"],
            ["observability", "regression isolation"],
            "Look for an ordered, falsifiable process: first confirm it's the "
            "deploy (correlate the timing, check if a rollback resolves it "
            "before deep-diving), then narrow with whatever observability "
            "exists - slow query logs, APM traces, flame graphs - rather than "
            "guessing. A strong answer names concrete likely causes for *this* "
            "deploy specifically (a missing index on a new query, an N+1 "
            "introduced by an ORM change, a new synchronous call in a hot "
            "path) rather than a generic performance checklist.",
        ),
        (
            "Design rate limiting for a public API. What are you protecting "
            "against, what algorithm would you use, and where does the limiter "
            "live?",
            "practical",
            ["rate-limiting", "api-design", "scalability"],
            ["token bucket", "distributed rate limiting"],
            "Should name what's being protected (abuse, accidental retry "
            "storms, one tenant starving others) and a specific algorithm - "
            "token bucket or sliding window, not just 'limit requests per "
            "minute' - with a reason for the choice (token bucket tolerates "
            "bursts; fixed window has a boundary-doubling edge case). For "
            "placement: a shared store like Redis if there's more than one API "
            "instance, since in-memory counters per instance don't actually "
            "enforce a global limit.",
        ),
        (
            "A React app's JS bundle has grown to 4MB and initial load is "
            "slow. How do you diagnose what's in it and bring it down?",
            "practical",
            ["performance", "bundling", "react"],
            ["code splitting", "bundle analysis"],
            "Diagnosis first: a bundle analyzer (webpack-bundle-analyzer or "
            "equivalent) to see what's actually taking the space, not guessing. "
            "Then concrete levers: route-based code splitting with "
            "`React.lazy`, checking for an accidentally-bundled large library "
            "(a moment-sized date library, a whole icon set imported "
            "wholesale), tree-shaking dead code, and lazy-loading anything not "
            "needed for first paint. A strong answer distinguishes diagnosis "
            "from fixing - don't start optimizing before measuring.",
        ),
        (
            "Two concurrent requests read the same database row, each compute "
            "an update, and write it back - the second write silently "
            "overwrites the first's change. How do you prevent this?",
            "scenario",
            ["concurrency", "race-conditions", "databases"],
            ["optimistic locking", "transactions", "atomic updates"],
            "Should recognize this as a lost-update race and name a real "
            "mechanism: an atomic `UPDATE ... SET x = x + 1` instead of "
            "read-then-write when possible, optimistic locking with a version "
            "column that rejects a write if the version has moved, or a "
            "transaction with `SELECT ... FOR UPDATE` to serialize the "
            "read-modify-write. Vague answers ('just use a transaction') "
            "without naming which mechanism actually prevents this specific "
            "race are a gap.",
        ),
        (
            "Walk through the common ways a JWT-based auth system gets built "
            "insecurely, and how you'd avoid each.",
            "theoretical",
            ["security", "authentication", "jwt"],
            ["token security", "session management"],
            "Should cover several of: storing the token in localStorage "
            "(readable by any injected script, vs an httpOnly cookie), "
            "accepting `alg: none` or trusting the client-specified algorithm "
            "instead of pinning it server-side, no expiry or a dangerously "
            "long one, no real revocation path (a stolen long-lived token is "
            "valid until it expires), and not validating the signature at all "
            "on every request. A strong answer treats this as a checklist of "
            "specific, nameable mistakes, not a vague 'JWTs can be insecure.'",
        ),
    ],
    # Level 4 - Advanced / Pressure Scenarios. System design and the
    # organizational judgment senior engineers are actually hired for.
    4: [
        (
            "Your product needs to handle 10x traffic growth over the next "
            "three months. Walk through what you'd look at first, and what "
            "you'd explicitly decide not to do yet.",
            "scenario",
            ["system-design", "scalability", "prioritization"],
            ["bottleneck identification", "capacity planning"],
            "A strong answer starts with measurement, not architecture: find "
            "the actual bottleneck (database? a synchronous external call? a "
            "single-instance stateful service?) rather than redesigning "
            "everything preemptively. Should distinguish cheap near-term "
            "levers (caching, read replicas, horizontal scaling of stateless "
            "services) from expensive ones (a full rewrite, premature "
            "sharding) and explicitly scope what's deferred - this is as much "
            "a prioritization question as a technical one.",
        ),
        (
            "Your team is split on microservices versus a monolith for a new "
            "product. How do you make that call, and how do you bring the team "
            "along with a decision some of them disagree with?",
            "scenario",
            ["system-design", "architecture-decisions", "leadership"],
            ["decision frameworks", "team alignment"],
            "Technically: ties the decision to the team's actual size, "
            "deployment cadence needs, and where the natural service "
            "boundaries are - not a default preference. A monolith is often "
            "the right call for a small team on a new product; microservices "
            "pay off when independent scaling or deployment is a real, present "
            "need. On the people side: look for a real decision process - "
            "written tradeoffs, a documented decision record, stating the "
            "conditions under which the decision would be revisited - rather "
            "than 'I just decided and told them.'",
        ),
        (
            "A table with 500 million rows needs a schema migration - adding a "
            "NOT NULL column - with zero downtime. Walk through how you'd do "
            "it.",
            "scenario",
            ["database-migrations", "system-design", "production-operations"],
            ["online schema changes", "backward-compatible migrations"],
            "A strong answer breaks this into safe, backward-compatible "
            "steps: add the column nullable first (adding a NOT NULL column "
            "with a default can lock the table on some databases at this "
            "scale), backfill in batches to avoid a long-running transaction "
            "and replication lag, then add the NOT NULL constraint once "
            "backfilled, and only then deploy code that depends on it being "
            "always present. Should flag the specific risk at this scale: a "
            "single-statement migration that locks or rewrites the whole table "
            "is the thing that causes downtime.",
        ),
        (
            "You inherit a codebase with no tests and a tight deadline for the "
            "next feature. How do you balance moving fast against the risk "
            "you're building on?",
            "scenario",
            ["technical-debt", "testing-strategy", "pragmatism"],
            ["risk-based testing", "incremental improvement"],
            "Look for pragmatism over dogma: not 'stop and write a full test "
            "suite first' (ignores the deadline) and not 'ship with no safety "
            "net' (ignores the risk). A strong answer targets tests at the "
            "highest-risk, highest-change surface - the code this feature "
            "actually touches - rather than the whole codebase, and treats "
            "testing debt as something paid down incrementally alongside "
            "feature work rather than a separate initiative nobody ever gets "
            "time for.",
        ),
        (
            "How do you decide whether a given feature needs strong "
            "consistency or can tolerate eventual consistency? Give a concrete "
            "example of each from systems you've worked with or studied.",
            "theoretical",
            ["distributed-systems", "consistency-models", "system-design"],
            ["CAP theorem trade-offs", "consistency guarantees"],
            "Should ground the decision in what happens when the guarantee is "
            "violated, not textbook definitions. Strong consistency where a "
            "stale read is actively harmful - an account balance before a "
            "withdrawal, an inventory count before confirming an order. "
            "Eventual consistency where a brief staleness is acceptable - a "
            "like count, a view counter, a search index. A strong answer can "
            "also name the cost of defaulting to strong consistency "
            "everywhere: it constrains availability and scaling in ways that "
            "aren't free.",
        ),
    ],
}


async def seed(*, interview_mode: str) -> int:
    from src.repository.crud.job_profile import JobProfileCRUDRepository
    from src.repository.database import async_db
    from src.models.db.job_profile import JobProfileStatus

    session = async_db.get_session()
    repo = JobProfileCRUDRepository(async_session=session)
    try:
        profile = await repo.create_profile(
            job_name=JOB_NAME,
            job_description=JOB_DESCRIPTION,
            experience_level="0-3 years",
            skills=SKILLS,
            category="it",
            employment_type="full-time",
            interview_mode=interview_mode,
        )

        questions = [
            {
                "job_profile_id": profile.id,
                "question_text": text,
                "level": level,
                "question_type": question_type,
                "is_ai_generated": False,
                "keywords": keywords,
                "concepts_covered": concepts,
                "expected_answer": expected_answer,
            }
            for level, items in QUESTION_BANK.items()
            for text, question_type, keywords, concepts, expected_answer in items
        ]
        await repo.create_job_profile_questions(questions)

        # Flagship content is vetted by construction - publish immediately
        # rather than leaving it to sit in draft/under_review, which would
        # make it invisible to students despite being ready.
        await repo.update_review_status(
            profile_id=profile.id,
            status=JobProfileStatus.PUBLISHED.value,
            admin_comment="Seeded flagship content - reviewed at authoring time.",
        )
        return profile.id
    finally:
        await session.close()


def _is_local_target() -> bool:
    from src.config.manager import settings

    return settings.DB_POSTGRES_HOST in LOCAL_HOSTS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--mode", choices=["practice", "assessment"], default="practice",
        help="interview_mode to seed the profile with (default: practice)",
    )
    parser.add_argument(
        "--i-know-what-im-doing", action="store_true",
        help="allow running against a non-local DB_POSTGRES_HOST",
    )
    args = parser.parse_args()

    if not args.i_know_what_im_doing and not _is_local_target():
        print(
            "Refusing to run: DB_POSTGRES_HOST is not a local database. "
            "This writes real rows - pass --i-know-what-im-doing if you really "
            "mean to seed the configured target.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    total_questions = sum(len(items) for items in QUESTION_BANK.values())
    profile_id = asyncio.run(seed(interview_mode=args.mode))
    print(
        f"Seeded job profile {profile_id} ({JOB_NAME}, mode={args.mode}): "
        f"{total_questions} questions across {len(QUESTION_BANK)} levels, published."
    )


if __name__ == "__main__":
    main()
