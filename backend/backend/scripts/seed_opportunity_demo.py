"""Example Coaching and Opportunities records for staging. Refuses every database except staging.

Run on the staging server, from backend/backend, after the demo learners are loaded:
    python scripts/seed_learner_hub_demo.py --seed
    python scripts/seed_opportunity_demo.py --seed
    python scripts/seed_opportunity_demo.py --remove

To reload the learners while these records exist, run --remove here first: these records
point at the demo learners and the demo admin, so deleting the learners would be blocked.

Everything is marked: "(demo)" names, a fixed request hash, or the demo interview track.
It only ever touches the .invalid demo learners, so no real person is notified or changed.
"""

import argparse
import asyncio
import datetime as dt
import hashlib
import os
import sys
import uuid

sys.path.insert(0, os.getcwd())

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

STAGING_DATABASE = "90e4ddde0ec04cb461be052445686f374e1fa118d3e0f9699501b9c42a60fbc1"
DOMAIN = "demo.samvaad.invalid"
MARK = " (demo)"
TRACK = "Demo practice data"
HASH = "demo".ljust(64, "0")
LOCKS = (
    "practice_shortlist protect_course_shortlist", "practice_shortlist_entry protect_course_shortlist_entry",
    "opportunity_selection opportunity_selection_immutable", "opportunity_selection_entry opportunity_selection_entry_immutable",
)
UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC)
DAY = dt.timedelta(days=1)
KEY = lambda: str(uuid.uuid4())  # noqa: E731
FB = {
    "include": ("Well done. You have been selected to go forward for this role.", "The placement team will share the next steps with you."),
    "exclude": ("Thank you for taking part. You were not selected for this role this time.", "Keep practising and watch for new openings."),
}


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
    if not local and hashlib.sha256(f"{url.host}/{url.database}".encode()).hexdigest() != STAGING_DATABASE:
        raise SystemExit("REFUSED: this is not the staging database. Example data is never written elsewhere.")


def imports():
    import src.models.db  # noqa: F401
    from src.models import db as _  # noqa: F401
    from src.models.db.bulk_ats import BulkATSBatch, BulkATSCandidate, BulkATSHandoffMessage, BulkATSPitch
    from src.models.db.coaching_assignment import CoachingAssignment
    from src.models.db.interview import Interview
    from src.models.db.job_profile import JobProfile
    from src.models.db.learning_plan import LearningPlan, LearningPlanAssignment, LearningPlanStep, LearningPlanStepProgress
    from src.models.db.mock_assignment import MockInterviewAssignment
    from src.models.db.opportunity import Opportunity, OpportunityCandidate, OpportunityRound
    from src.models.db.opportunity_handoff import OpportunityHandoff, OpportunityHandoffCandidate
    from src.models.db.opportunity_selection import OpportunitySelection, OpportunitySelectionEntry
    from src.models.db.practice_governance import PracticeShortlist, PracticeShortlistEntry
    from src.models.db.summary_report import SummaryReport
    from src.models.db.user import User
    return locals()


async def remove(factory) -> dict:
    m = imports()
    out = {}
    async with factory() as s:
        # Finalized shortlists and locked selections are protected by triggers. They are off
        # only inside this one transaction and back on before it commits, so the app never
        # sees them off; only demo rows are deleted meanwhile.
        for lock in LOCKS:
            await s.execute(sa.text("ALTER TABLE {} DISABLE TRIGGER {}".format(*lock.split())))
        opp = sa.select(m["Opportunity"].id).where(m["Opportunity"].organisation.like(f"%{MARK}"))
        handoffs = sa.select(m["OpportunityHandoff"].id).where(m["OpportunityHandoff"].opportunity_id.in_(opp))
        selections = sa.select(m["OpportunitySelection"].id).where(m["OpportunitySelection"].opportunity_id.in_(opp))
        await s.execute(sa.delete(m["OpportunityHandoffCandidate"]).where(m["OpportunityHandoffCandidate"].handoff_id.in_(handoffs)))
        await s.execute(sa.delete(m["OpportunityHandoff"]).where(m["OpportunityHandoff"].opportunity_id.in_(opp)))
        await s.execute(sa.delete(m["OpportunitySelectionEntry"]).where(m["OpportunitySelectionEntry"].selection_id.in_(selections)))
        await s.execute(sa.delete(m["OpportunitySelection"]).where(m["OpportunitySelection"].opportunity_id.in_(opp)))
        await s.execute(sa.delete(m["OpportunityRound"]).where(m["OpportunityRound"].opportunity_id.in_(opp)))
        await s.execute(sa.delete(m["OpportunityCandidate"]).where(m["OpportunityCandidate"].opportunity_id.in_(opp)))
        await s.execute(sa.update(m["BulkATSBatch"]).where(m["BulkATSBatch"].opportunity_id.in_(opp)).values(opportunity_id=None))
        out["openings"] = (await s.execute(sa.delete(m["Opportunity"]).where(m["Opportunity"].organisation.like(f"%{MARK}")))).rowcount
        runs = sa.select(m["BulkATSBatch"].id).where(m["BulkATSBatch"].name.like(f"%{MARK}"))
        people = sa.select(m["BulkATSCandidate"].id).where(m["BulkATSCandidate"].batch_id.in_(runs))
        await s.execute(sa.delete(m["BulkATSPitch"]).where(m["BulkATSPitch"].candidate_id.in_(people)))
        await s.execute(sa.delete(m["BulkATSHandoffMessage"]).where(m["BulkATSHandoffMessage"].candidate_id.in_(people)))
        await s.execute(sa.delete(m["BulkATSCandidate"]).where(m["BulkATSCandidate"].batch_id.in_(runs)))
        out["runs"] = (await s.execute(sa.delete(m["BulkATSBatch"]).where(m["BulkATSBatch"].name.like(f"%{MARK}")))).rowcount
        for name in ("CoachingAssignment", "MockInterviewAssignment"):
            out[name] = (await s.execute(sa.delete(m[name]).where(m[name].request_hash == HASH))).rowcount
        await s.execute(sa.delete(m["LearningPlanStepProgress"]).where(m["LearningPlanStepProgress"].assignment_id.in_(sa.select(m["LearningPlanAssignment"].id).where(m["LearningPlanAssignment"].request_hash == HASH))))
        await s.execute(sa.delete(m["LearningPlanAssignment"]).where(m["LearningPlanAssignment"].request_hash == HASH))
        plans = sa.select(m["LearningPlan"].id).where(m["LearningPlan"].title.like(f"%{MARK}"))
        await s.execute(sa.delete(m["LearningPlanStep"]).where(m["LearningPlanStep"].plan_id.in_(plans)))
        out["plans"] = (await s.execute(sa.delete(m["LearningPlan"]).where(m["LearningPlan"].title.like(f"%{MARK}")))).rowcount
        lists = sa.select(m["PracticeShortlist"].id).where(m["PracticeShortlist"].name.like(f"%{MARK}"))
        await s.execute(sa.delete(m["PracticeShortlistEntry"]).where(m["PracticeShortlistEntry"].shortlist_id.in_(lists)))
        out["shortlists"] = (await s.execute(sa.delete(m["PracticeShortlist"]).where(m["PracticeShortlist"].name.like(f"%{MARK}")))).rowcount
        ivs = sa.select(m["Interview"].id).where(m["Interview"].track == TRACK)
        await s.execute(sa.delete(m["SummaryReport"]).where(m["SummaryReport"].interview_id.in_(ivs)))
        out["interviews"] = (await s.execute(sa.delete(m["Interview"]).where(m["Interview"].track == TRACK))).rowcount
        for lock in LOCKS:
            await s.execute(sa.text("ALTER TABLE {} ENABLE TRIGGER {}".format(*lock.split())))
        await s.commit()
    return out


def band(v):
    return "Excellent" if v >= 85 else "Good" if v >= 70 else "Average" if v >= 50 else "Weak"


async def seed_opening(s, m, actor, pool, used, spec, tpl):
    from src.services import opportunities as access
    from src.services import opportunity_selection as service

    O, C, R = m["Opportunity"], m["OpportunityCandidate"], m["OpportunityRound"]
    opening = O(course_id=spec["course"], title=spec["title"], organisation=spec["org"] + MARK, job_profile_id=tpl[spec["tpl"]],
                requirements="Clear communication and comfort with the core tools for this role.", max_evidence_age_days=90,
                min_assessments=1, status="open", created_by=actor.id, request_key=KEY(), request_hash=HASH, created_at=NOW - 6 * DAY)
    s.add(opening)
    await s.flush()
    for i, (kind, name) in enumerate(spec["rounds"], 1):
        s.add(R(opportunity_id=opening.id, position=i, kind=kind, name=name, job_profile_id=tpl[spec["tpl"]] if kind == "ai" else None, created_by=actor.id))
    counts = {k: spec.get(k, 0) for k in ("need", "todo", "inc", "exc")}
    total = sum(counts.values())
    free = [p for p in pool[spec["course"]] if p not in used]
    known = await service.evidence(s, opening, free) if free else {}
    fresh = [p for p in free if not known[p]["assessments"]]
    need = fresh[: counts["need"]]
    rest = [p for p in free if p not in need][: total - len(need)]
    used.update(need + rest)
    for sid in need + rest:
        s.add(C(opportunity_id=opening.id, student_id=sid, added_by=actor.id))
    for sid in rest:
        if not known[sid]["assessments"]:
            iv = m["Interview"](user_id=sid, track=TRACK, course_id=spec["course"], interview_mode="assessment", status="completed", job_profile_id=tpl[spec["tpl"]],
                                created_at=NOW - 8 * DAY, completed_at=NOW - 8 * DAY)
            s.add(iv)
            await s.flush()
            s.add(m["SummaryReport"](interview_id=iv.id, report_json={"overallScoreSummary": {"knowledgeCompetence": {"percentage": 72}, "speechStructure": {"percentage": 68}}}))
    await s.flush()
    if not rest:
        if spec.get("status"):
            opening.status = spec["status"]
        return opening
    selection = m["OpportunitySelection"](opportunity_id=opening.id, revision=1, version=len(rest) + 1, status="draft", rules=service.rules(opening), created_by=actor.id)
    s.add(selection)
    await s.flush()
    ev = await service.evidence(s, opening, rest)
    todo, inc, exc = rest[: counts["todo"]], rest[counts["todo"] : counts["todo"] + counts["inc"]], rest[counts["todo"] + counts["inc"] :]
    E = m["OpportunitySelectionEntry"]
    for i, sid in enumerate(todo):
        if i < spec.get("rec", 0):
            s.add(E(selection_id=selection.id, student_id=sid, recommendation="include", recommendation_reason="Explained the project clearly and answered follow-ups well.", recommended_by=actor.id, recommended_at=NOW - DAY))
    for sid, d, why in [*((x, "include", "Strong interviews and a clear fit for this role.") for x in inc), *((x, "exclude", "Did not yet show the depth this role needs.") for x in exc)]:
        s.add(E(selection_id=selection.id, student_id=sid, decision=d, decision_reason=why, student_feedback=FB[d][0], next_step=FB[d][1], decided_by=actor.id, decided_at=NOW - DAY, evidence_snapshot=ev[sid]))
    await s.flush()
    if spec.get("selection") in ("locked", "released"):
        predicate = await access.authorize(actor, spec["course"], s, "read")
        view = await service.detail(s, actor, opening, selection, predicate)
        selection.snapshot = {"title": opening.title, "organisation": opening.organisation, "rules": selection.rules, "items": view["items"]}
        selection.status, selection.locked_by, selection.locked_at = "locked", actor.id, NOW - 2 * DAY
        if spec["selection"] == "released":
            selection.status, selection.released_by, selection.released_at = "released", actor.id, NOW - DAY
            selection.release_key, selection.release_reason = KEY(), "Example data: decisions reviewed."
    if spec.get("sent"):
        h = m["OpportunityHandoff"](opportunity_id=opening.id, selection_id=selection.id, status="shared", version=2, contact_name="Neha Rao", contact_email=f"neha@{DOMAIN}", channel="email", created_by=actor.id, shared_by=actor.id, shared_at=NOW - DAY)
        s.add(h)
        await s.flush()
        for i, sid in enumerate(inc):
            s.add(m["OpportunityHandoffCandidate"](handoff_id=h.id, student_id=sid, company_status=["interviewing", "sent", "offered", "joined"][i % 4], updated_by=actor.id))
    if spec.get("status"):
        opening.status = spec["status"]
    await s.flush()
    return opening


RUNS = [
    ("Acme SDE Intern", "SDE Intern", "fresher", ["Python", "SQL", "Git"], "Acme"),
    ("Northwind analyst", "Data Analyst", "fresher", ["SQL", "Excel"], None),
    ("Zenlytics Web Developer", "Web Developer", "experienced", ["React", "CSS"], None),
]
PEOPLE = [
    # name, run, score, decision, hand-off, extra
    ("Aman Gupta", 0, 91, "shortlist", "screened", {}), ("Sara Minz", 0, 88, "shortlist", "screened", {}), ("Neha Kujur", 0, 84, "shortlist", "screened", {}),
    ("Imran Ansari", 0, 79, "shortlist", "screened", {}), ("Anjali Tirkey", 2, 81, "shortlist", "screened", {}), ("Pooja Lakra", 1, 76, "shortlist", "screened", {}),
    ("Vikash Oraon", 1, 73, "shortlist", "screened", {}), ("Rahul Mahto", 2, 69, "shortlist", "screened", {}),
    ("Priyanka Sahu", 0, 86, None, "screened", {}), ("Deepak Oraon", 0, 78, None, "screened", {}), ("Tina Marandi", 0, 74, None, "screened", {"flag": True}),
    ("Vivek Kumar", 0, 66, None, "screened", {}), ("Nandini Bara", 0, 59, None, "screened", {}), ("Ajay Tirkey", 1, 83, None, "screened", {}), ("Ruchi Lakra", 1, 71, None, "screened", {}),
    ("Sonu Minz", 1, 63, None, "screened", {"flag": True}), ("Komal Xalxo", 2, 80, None, "screened", {}), ("Harsh Ekka", 2, 68, None, "screened", {}),
    ("Sunil Bhagat", 0, 58, "hold", "screened", {}), ("Kavita Soren", 1, 44, "not_shortlisted", "screened", {}),
    ("Tanvi Ekka", 2, 72, "shortlist", "shared_with_placement", {"rate": 7, "note": "Steady in the mock rounds.", "ask": "Can they join in January, or only after exams?", "reply": "They can join from 12 January."}),
    ("Deepa Kachhap", 0, 90, "shortlist", "shared_with_placement", {"rate": 9, "note": "Led the capstone team and explains her work clearly."}),
    ("Manish Lohra", 0, 86, "shortlist", "shared_with_placement", {"rate": 8, "note": "Strong in data structures."}), ("Sneha Bara", 0, 83, "shortlist", "shared_with_placement", {"rate": 8}),
    ("Ritika Xaxa", 2, 88, "shortlist", "shared_with_placement", {"rate": 9, "note": "Built and deployed two full websites."}), ("Arjun Munda", 1, 78, "shortlist", "shared_with_placement", {"rate": 7}),
    ("Nitin Pahan", 1, 67, "shortlist", "shared_with_placement", {"rate": 6}),
    ("Shreya Minz", 0, 82, "shortlist", "needs_info", {"rate": 8, "ask": "Did they build the payments project alone, or in a team?"}), ("Rohan Tiga", 1, 74, "shortlist", "needs_info", {"rate": 7, "ask": "Has this learner worked with SQL on real data?"}),
    ("Bhavna Hembrom", 2, 80, "shortlist", "needs_info", {"rate": 8, "ask": "Are they open to working from Bengaluru?"}),
    ("Ankit Bediya", 0, 89, "shortlist", "accepted_in_placement", {"rate": 9, "pn": "Good fit for the January drive."}), ("Payal Kerketta", 0, 85, "shortlist", "accepted_in_placement", {"rate": 8}),
    ("Suraj Hansda", 2, 83, "shortlist", "accepted_in_placement", {"rate": 8}), ("Mina Murmu", 1, 77, "shortlist", "accepted_in_placement", {"rate": 7}),
    ("Gaurav Soy", 0, 92, "shortlist", "referred_to_hr", {"rate": 10, "co": "Acme", "ok": True}), ("Isha Barla", 0, 87, "shortlist", "referred_to_hr", {"rate": 9, "co": "Acme", "ok": True}),
    ("Kunal Purty", 1, 81, "shortlist", "referred_to_hr", {"rate": 8, "co": "Northwind", "ok": True}), ("Lata Baskey", 2, 86, "shortlist", "referred_to_hr", {"rate": 9, "co": "Zenlytics", "ok": False}),
    ("Mohit Soreng", 0, 62, "shortlist", "rejected", {"rate": 5, "why": "This role needs stronger backend experience."}), ("Rekha Tudu", 1, 55, "shortlist", "rejected", {"rate": 5, "why": "Not available for the joining date."}),
    ("Nikita Barwa", 0, None, None, "screened", {"failed": True}),
]


async def seed_runs(s, m, actor, acme_id):
    runs = []
    for name, role, level, skills, company in RUNS:
        b = m["BulkATSBatch"](name=name + MARK, role_name=role, experience_mode=level, required_skills=skills, role_description=f"Example job description for the {role} role, with the responsibilities and skills it needs.", status="completed", created_by=actor.id, creator_name=actor.name, opportunity_id=acme_id if company == "Acme" else None)
        s.add(b)
        await s.flush()
        runs.append(b)
    Msg, by = m["BulkATSHandoffMessage"], actor.name or "Staff"
    for i, (name, run, score, decision, state, x) in enumerate(PEOPLE):
        email = name.lower().replace(" ", ".") + "@" + DOMAIN
        c = m["BulkATSCandidate"](batch_id=runs[run].id, candidate_name=name, email_or_candidate_id=email, final_score=score, score_band=band(score) if score else None,
                                  status="failed" if x.get("failed") else ("needs_review" if x.get("flag") else "completed"), needs_review=bool(x.get("flag")),
                                  warnings=["listed_skills_need_verification"] if x.get("flag") else [], confidence_score=0.62 + (i % 8) * 0.04 if score else None,
                                  reviewer_decision=decision, error_message="This file could not be read." if x.get("failed") else None, handoff_status=state,
                                  assigned_reviewer_id=actor.id if i % 5 == 0 else None, interview_outcome="not_interviewed")
        if state != "screened":
            c.program_readiness_score, c.program_feedback_notes = x["rate"], x.get("note")
            c.program_reviewed_by, c.program_reviewed_at, c.handoff_shared_at = actor.id, NOW - (2 + i % 4) * DAY, NOW - (2 + i % 4) * DAY
        if state in ("accepted_in_placement", "referred_to_hr", "rejected"):
            c.placement_decided_by, c.placement_decided_at = actor.id, NOW - DAY
        c.placement_notes = x.get("pn")
        s.add(c)
        await s.flush()
        day = NOW - (2 + i % 4) * DAY
        if state != "screened":
            s.add(Msg(candidate_id=c.id, author_id=actor.id, author_name=by, kind="shared", body=f"Rated {x['rate']} of 10. {x.get('note') or ''}".strip(), created_at=day))
        if x.get("ask"):
            s.add(Msg(candidate_id=c.id, author_id=actor.id, author_name=by, kind="question", body=x["ask"], created_at=day + DAY / 2))
        if x.get("reply"):
            s.add(Msg(candidate_id=c.id, author_id=actor.id, author_name=by, kind="reply", body=x["reply"], created_at=day + DAY))
        if state in ("accepted_in_placement", "referred_to_hr"):
            s.add(Msg(candidate_id=c.id, author_id=actor.id, author_name=by, kind="accepted", body="", created_at=day + DAY))
        if x.get("why"):
            s.add(Msg(candidate_id=c.id, author_id=actor.id, author_name=by, kind="declined", body=x["why"], created_at=day + DAY))
        if x.get("co"):
            hr = {"Acme": "Priya Sharma", "Northwind": "Karan Mehta", "Zenlytics": "Ritu Nair"}[x["co"]]
            mail = f"{hr.split()[0].lower()}@{DOMAIN}"
            s.add(m["BulkATSPitch"](candidate_id=c.id, company_name=x["co"], hr_name=hr, hr_email=mail, include_resume_links=True, delivered=x["ok"], sent_by=actor.id))
            c.placement_assigned_companies = [x["co"]]
            s.add(Msg(candidate_id=c.id, author_id=actor.id, author_name=by, kind="pitched" if x["ok"] else "pitch_failed", body=f"{hr}, {mail} at {x['co']}", created_at=NOW - DAY))
    await s.flush()


async def seed_coaching(s, m, actor, learners, tpl_mock):
    CA, MA, PA = m["CoachingAssignment"], m["MockInterviewAssignment"], m["LearningPlanAssignment"]
    modules = ["warm_up", "think_fast", "pronunciation", "structure", "pace"]
    why = "Example: this practice will help with the weaker part of recent answers."
    base = lambda who: dict(student_id=who.id, assigned_by=actor.id, course_id=who.enrolled_course_ids[0], reason=why, request_key=KEY(), request_hash=HASH, version=1)  # noqa: E731
    # Start after the people most likely to appear under Need help, so that list stays useful.
    people = learners[40:]
    for i, who in enumerate(people[:60]):
        mod = modules[i % 5]
        if i < 26:
            s.add(CA(**base(who), module=mod, due_date=(NOW + (2 + i % 9) * DAY).date(), status="assigned", note="Try this before Friday." if i % 4 == 0 else None))
        elif i < 38:
            s.add(CA(**base(who), module=mod, due_date=(NOW - (1 + i % 6) * DAY).date(), status="assigned"))
        elif i < 54:
            s.add(CA(**base(who), module=mod, due_date=(NOW - i % 5 * DAY).date(), status="completed", closed_at=NOW - (1 + i % 10) * DAY))
        else:
            s.add(CA(**base(who), module=mod, status="cancelled", closed_at=NOW - 3 * DAY))
    for i, who in enumerate(people[60:72]):
        course = who.enrolled_course_ids[0]
        if course in tpl_mock:
            s.add(MA(**base(who), job_profile_id=tpl_mock[course], due_date=(NOW + (3 if i % 3 else -2) * DAY).date(), status="assigned"))
    await s.flush()
    P, Step, Done = m["LearningPlan"], m["LearningPlanStep"], m["LearningPlanStepProgress"]
    specs = [
        ("Explain your project clearly", "Talk through a project so anyone can follow it.", "published", [("drill", "Warm up first", "warm_up"), ("drill", "Give your answer a clear shape", "structure"), ("reflection", "What will you say differently?", None)]),
        ("First interview ready", "Walk into a first interview feeling prepared.", "published", [("drill", "Quick answers", "think_fast"), ("drill", "Steady speaking", "pace"), ("reflection", "What went well?", None)]),
        ("Clear speaking basics", "Be easy to understand when you speak.", "draft", [("drill", "Say difficult words clearly", "pronunciation")]),
        ("Old intro plan", "An earlier plan kept for its history.", "archived", [("drill", "Warm up", "warm_up")]),
    ]
    made = []
    for title, goal, status, steps in specs:
        p = P(title=title + MARK, goal=goal, status=status, version=1, row_version=1, request_key=KEY(), created_by=actor.id, self_start=title.startswith("First"), published_at=NOW - 20 * DAY if status != "draft" else None)
        s.add(p)
        await s.flush()
        rows = []
        for n, (kind, text, module) in enumerate(steps, 1):
            row = Step(plan_id=p.id, position=n, kind=kind, module=module, title=text)
            s.add(row)
            rows.append(row)
        await s.flush()
        made.append((p, rows))
    for i, who in enumerate(people[72:96]):
        p, rows = made[i % 2]
        state = "active" if i < 18 else "cancelled" if i > 21 else "active"
        a = PA(plan_id=p.id, student_id=who.id, course_id=who.enrolled_course_ids[0], source="self" if i % 6 == 5 else "assigned", assigned_by=None if i % 6 == 5 else actor.id, reason=None if i % 6 == 5 else why, due_date=(NOW + (5 - i % 9) * DAY).date(), status=state, version=1, request_key=KEY(), request_hash=HASH, closed_at=NOW - DAY if state == "cancelled" else None)
        s.add(a)
        await s.flush()
        for row in rows[: i % 3]:
            s.add(Done(assignment_id=a.id, step_id=row.id, evidence_kind="reflection" if row.kind == "reflection" else "drill_completion", reflection="I will start with the result, then explain how I got there." if row.kind == "reflection" else None, completed_at=NOW - DAY))
    for i, (who, state) in enumerate(zip(learners[100:112], ["failed", "failed", "terminated", "active", "active", "paused", "completed", "completed", "completed", "completed", "completed", "completed"])):
        s.add(m["Interview"](user_id=who.id, track=TRACK, course_id=who.enrolled_course_ids[0], interview_mode="practice", status=state, created_at=NOW - (3 + i) * dt.timedelta(hours=2), completed_at=NOW - 3 * dt.timedelta(hours=1) if state == "completed" else None))
    await s.flush()
    PS, PE = m["PracticeShortlist"], m["PracticeShortlistEntry"]
    ds = [p for p in learners if p.enrolled_course_ids == ["demo-ds"]][:14]
    rules = {"role": "Data Analyst", "maximum_evidence_age_days": 90, "minimum_completed_interviews": 1, "decision_mode": "human_only"}
    for name, state, take in [("October analyst opportunities", "draft", 6), ("SDE Intern practice review", "locked", 8)]:
        # A finalized shortlist takes no new entries, so it is filled first and finalized after.
        sl = PS(course_id="demo-ds", name=name + MARK, opportunity=name, evidence_rules=rules, status="draft", version=1, created_by=actor.id, expires_at=NOW + 25 * DAY, request_key=KEY(), request_hash=HASH)
        s.add(sl)
        await s.flush()
        for i, who in enumerate(ds[:take]):
            s.add(PE(shortlist_id=sl.id, student_id=who.id, decision="exclude" if i % 3 == 2 else "include", reason="Example: reviewed against the role.", evidence_snapshot={"completed_practice": 2, "latest_completed_at": None, "state": "available"}, decided_by=actor.id))
        await s.flush()
        if state == "locked":
            await s.execute(sa.update(PS).where(PS.id == sl.id).values(status="locked", locked_by=actor.id, locked_at=NOW - DAY).execution_options(synchronize_session=False))
    await s.flush()


OPENINGS = [
    dict(title="SDE Intern", org="Acme", course="demo-ds", tpl="SDE Intern", rounds=[("ai", "Technical round"), ("human", "HR round")], need=3, todo=6, rec=3, inc=4, exc=1),
    dict(title="Data Analyst", org="Northwind", course="demo-ds", tpl="Data Analyst", rounds=[("ai", "Case round")], need=5),
    dict(title="Frontend Developer", org="Zenith", course="demo-web", tpl="Frontend Developer", rounds=[("ai", "Technical round")]),
    dict(title="UI Engineer", org="Orbit", course="demo-web", tpl="Frontend Developer", rounds=[("ai", "Technical round"), ("human", "Final round")], inc=5, exc=3, selection="locked"),
    dict(title="Business Analyst", org="Lumen", course="demo-ds", tpl="Data Analyst", rounds=[("ai", "Case round")], inc=4, exc=2, selection="released", sent=True),
    dict(title="Support Engineer", org="Delta", course="demo-web", tpl="Frontend Developer", rounds=[("human", "Screening")], inc=2, exc=1, selection="released", status="closed"),
]


async def seed(factory) -> dict:
    await remove(factory)
    m = imports()
    U, J = m["User"], m["JobProfile"]
    async with factory() as s:
        actor = (await s.execute(sa.select(U).where(U.is_admin.is_(True)).order_by(U.id).limit(1))).scalar_one_or_none()
        if actor is None:
            # A fresh database has no administrator yet. A demo one owns the records; it
            # uses a .invalid address and has no password, so nobody can sign in as it.
            actor = (await s.execute(sa.select(U).where(U.email == f"admin@{DOMAIN}"))).scalar_one_or_none()
            if actor is None:
                actor = U(email=f"admin@{DOMAIN}", name="Demo Admin", password_hash="demo-account-cannot-sign-in", is_admin=True)
                s.add(actor)
                await s.flush()
        learners = list((await s.execute(sa.select(U).where(U.email.like(f"%@{DOMAIN}"), U.enrolled_course_ids.is_not(None)).order_by(U.id))).scalars())
        learners = [p for p in learners if p.enrolled_course_ids]
        if len(learners) < 120:
            raise SystemExit("Load the demo learners first: scripts/seed_learner_hub_demo.py --seed")
        rows = (await s.execute(sa.select(J.id, J.job_name, J.interview_mode, J.course_ids).where(J.job_name.like(f"%{MARK}"), J.status == "published").order_by(J.id))).all()
        tpl = {r.job_name.replace(MARK, ""): r.id for r in rows if r.interview_mode == "assessment"}
        mocks = {}
        for r in rows:
            if r.interview_mode == "practice":
                for c in r.course_ids or []:
                    mocks[c] = r.id
        pool = {c: [p.id for p in learners[112:] if p.enrolled_course_ids == [c]] for c in ("demo-ds", "demo-web")}
        used: set[int] = set()
        first = None
        for spec in OPENINGS:
            opening = await seed_opening(s, m, actor, pool, used, spec, tpl)
            first = first or opening.id
        await seed_runs(s, m, actor, first)
        await seed_coaching(s, m, actor, learners, mocks)
        await s.commit()
    return {"openings": len(OPENINGS), "runs": len(RUNS), "resumes": len(PEOPLE)}


async def main() -> None:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--seed", action="store_true")
    action.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    url = database_url()
    refuse_unless_staging(url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        print("removed" if args.remove else "seeded", await (remove(factory) if args.remove else seed(factory)))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
