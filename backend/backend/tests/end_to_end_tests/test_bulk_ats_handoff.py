"""Real SQL tests for Program to Placement: the order of steps, who may act, and who gets told."""

import fastapi
import httpx
import pytest
import sqlalchemy as sa

from src.api.dependencies.admin import get_current_bulk_ats_user
from src.api.dependencies.session import get_async_session
from src.api.routes import bulk_ats_handoff as ho
from src.models.db.bulk_ats import BulkATSBatch, BulkATSCandidate, BulkATSPitch
from src.models.db.notification import Notification
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


@pytest.fixture
async def ctx(db, monkeypatch):
    _, factory, _ = db
    async with factory() as s:
        await s.execute(sa.delete(User))
        users = {}
        for name, admin in [("admin", True), ("prog", False), ("place", False), ("other", False)]:
            u = User(email=f"{name}@h.test", name=name, password_hash="t", is_admin=admin)
            s.add(u)
            await s.flush()
            users[name] = u.id
        team = Team(name="T")
        s.add(team)
        await s.flush()
        batch = BulkATSBatch(name="Acme run", role_name="SDE", created_by=users["prog"])
        s.add(batch)
        await s.flush()
        ids = {}
        for name, decision, status in [("aman", "shortlist", "completed"), ("sara", "shortlist", "completed"), ("hold", "hold", "completed")]:
            c = BulkATSCandidate(batch_id=batch.id, candidate_name=name, email_or_candidate_id=f"{name}@x.t", final_score=80, score_band="Good", status=status, reviewer_decision=decision)
            s.add(c)
            await s.flush()
            ids[name] = c.id
        await s.commit()
    who = {"prog": ho.Who(program=True, placement=False), "place": ho.Who(program=False, placement=True), "admin": ho.Who(program=True, placement=True), "other": ho.Who(program=False, placement=False)}
    names = {v: k for k, v in users.items()}

    async def fake_who(user, session):
        return who[names[user.id]]

    async def fake_people(session):
        return {users["place"], users["admin"]}

    async def fake_summary(**k):
        return "Good people."

    monkeypatch.setattr(ho, "who_is", fake_who)
    monkeypatch.setattr(ho, "placement_people", fake_people)
    monkeypatch.setattr(ho, "generate_company_pitch_summary", fake_summary)
    monkeypatch.setattr(ho, "build_company_pitch_email", lambda **k: ("s", "h", "t"))
    sent = {"ok": True}
    monkeypatch.setattr(ho, "send_email", lambda **k: sent["ok"])
    app = fastapi.FastAPI()
    app.include_router(ho.router)

    async def session_dep():
        async with factory() as s:
            try:
                yield s
            finally:
                await s.rollback()

    async def actor(request: fastapi.Request, session=fastapi.Depends(get_async_session)):
        return await session.get(User, users[request.headers["x-a"]])

    app.dependency_overrides[get_async_session] = session_dep
    app.dependency_overrides[get_current_bulk_ats_user] = actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, users, ids, factory, sent


H = lambda w: {"x-a": w}  # noqa: E731
P = "/bulk-ats/handoff"
PITCH = {"company_name": "Acme", "hr_name": "Priya", "hr_email": "p@acme.example.com"}


async def share(c, ids):
    r = await c.post(f"{P}/share", headers=H("prog"), json={"candidate_ids": ids, "readiness_score": 8, "notes": "Good"})
    assert r.status_code == 200, r.text


async def status(factory, cid):
    async with factory() as s:
        return (await s.get(BulkATSCandidate, cid)).handoff_status


async def kinds(factory):
    async with factory() as s:
        return list((await s.execute(sa.select(Notification.recipient_id, Notification.kind, Notification.href).order_by(Notification.id))).all())


async def test_cards_match_and_placement_never_sees_unshared(ctx):
    c, users, ids, factory, _ = ctx
    prog = (await c.get(f"{P}?card=toshare", headers=H("prog"))).json()
    assert prog["counts"]["toshare"] == 2 == prog["total"]
    place = (await c.get(f"{P}?card=toshare", headers=H("place"))).json()
    assert place["counts"]["toshare"] == 0 and place["items"] == []
    found = (await c.get(f"{P}?q=aman", headers=H("place"))).json()
    assert found["total"] == 0
    assert (await c.get(P, headers=H("other"))).status_code == 403
    assert (await c.get(f"{P}/{ids['aman']}", headers=H("place"))).status_code == 404


async def test_full_journey_tells_each_team(ctx):
    c, users, ids, factory, _ = ctx
    a = ids["aman"]
    await share(c, [a])
    assert await status(factory, a) == "shared_with_placement"
    n = await kinds(factory)
    assert {x[0] for x in n} == {users["place"], users["admin"], users["prog"]} and "card=waiting" in n[0][2]
    assert (await c.post(f"{P}/ask", headers=H("place"), json={"candidate_ids": [a], "question": "Can they relocate?"})).status_code == 200
    assert await status(factory, a) == "needs_info"
    n = await kinds(factory)
    assert n[-1][0] == users["prog"] and "card=info" in n[-1][2]
    row = (await c.get(f"{P}?card=info", headers=H("prog"))).json()["items"][0]
    assert row["question"] == "Can they relocate?"
    assert (await c.post(f"{P}/reply", headers=H("prog"), json={"candidate_id": a, "reply": "Yes, from Feb."})).status_code == 200
    n = await kinds(factory)
    assert n[-1][0] == users["place"]
    row = (await c.get(f"{P}?card=waiting", headers=H("place"))).json()["items"][0]
    assert row["reply"] == "Yes, from Feb." and row["readiness"] == 8
    assert (await c.post(f"{P}/accept", headers=H("place"), json={"candidate_ids": [a]})).status_code == 200
    assert (await c.post(f"{P}/pitch", headers=H("place"), json={"candidate_ids": [a], **PITCH})).json() == {"delivered": 1, "failed": 0}
    assert await status(factory, a) == "referred_to_hr"
    who = (await c.get(f"{P}/{a}", headers=H("prog"))).json()
    assert [h["kind"] for h in who["history"]] == ["pitched", "accepted", "reply", "question", "shared"]


async def test_order_is_enforced(ctx):
    c, users, ids, factory, _ = ctx
    a = ids["aman"]
    assert (await c.post(f"{P}/accept", headers=H("place"), json={"candidate_ids": [a]})).status_code == 409
    await share(c, [a])
    assert (await c.post(f"{P}/share", headers=H("prog"), json={"candidate_ids": [a], "readiness_score": 3})).status_code == 409
    assert (await c.post(f"{P}/reply", headers=H("prog"), json={"candidate_id": a, "reply": "Hello"})).status_code == 409
    await c.post(f"{P}/accept", headers=H("place"), json={"candidate_ids": [a]})
    assert await status(factory, a) == "accepted_in_placement"
    again = await c.post(f"{P}/share", headers=H("prog"), json={"candidate_ids": [a], "readiness_score": 2})
    assert again.status_code == 409 and await status(factory, a) == "accepted_in_placement"


async def test_teams_cannot_do_each_others_steps(ctx):
    c, users, ids, factory, _ = ctx
    a = ids["aman"]
    assert (await c.post(f"{P}/share", headers=H("place"), json={"candidate_ids": [a], "readiness_score": 8})).status_code == 403
    await share(c, [a])
    assert (await c.post(f"{P}/accept", headers=H("prog"), json={"candidate_ids": [a]})).status_code == 403
    assert (await c.post(f"{P}/decline", headers=H("prog"), json={"candidate_ids": [a], "reason": "Not a fit for now."})).status_code == 403


async def test_decline_needs_reason_and_can_move_back(ctx):
    c, users, ids, factory, _ = ctx
    a = ids["aman"]
    await share(c, [a])
    assert (await c.post(f"{P}/decline", headers=H("place"), json={"candidate_ids": [a], "reason": "no"})).status_code == 422
    await c.post(f"{P}/decline", headers=H("place"), json={"candidate_ids": [a], "reason": "Needs stronger SQL here."})
    row = (await c.get(f"{P}?card=declined", headers=H("prog"))).json()["items"][0]
    assert row["decline_reason"] == "Needs stronger SQL here."
    await c.post(f"{P}/move-back", headers=H("place"), json={"candidate_ids": [a]})
    assert await status(factory, a) == "shared_with_placement"


async def test_failed_email_is_recorded_and_can_be_resent(ctx):
    c, users, ids, factory, sent = ctx
    a = ids["aman"]
    await share(c, [a])
    sent["ok"] = False
    assert (await c.post(f"{P}/pitch", headers=H("place"), json={"candidate_ids": [a], **PITCH})).json() == {"delivered": 0, "failed": 1}
    row = (await c.get(f"{P}?card=sent", headers=H("place"))).json()["items"][0]
    assert row["pitch"]["delivered"] is False
    sent["ok"] = True
    assert (await c.post(f"{P}/pitches/{row['pitch']['id']}/resend", headers=H("place"))).json()["delivered"] == 1
    row = (await c.get(f"{P}?card=sent", headers=H("place"))).json()["items"][0]
    assert row["pitch"]["delivered"] is True
    assert (await c.post(f"{P}/pitches/{row['pitch']['id']}/resend", headers=H("place"))).status_code == 409
    async with factory() as s:
        assert (await s.execute(sa.select(sa.func.count()).select_from(BulkATSPitch))).scalar_one() == 2


async def test_share_with_target_team_and_metadata(ctx):
    c, users, ids, factory, _ = ctx
    a = ids["aman"]
    res = await c.post(
        f"{P}/share",
        headers=H("prog"),
        json={
            "candidate_ids": [a],
            "readiness_score": 9,
            "notes": "Shortlisted candidates with strong skills.",
            "target_queue": "Placement Team",
        },
    )
    assert res.status_code == 200
    assert res.json() == {"shared": 1}

    async with factory() as session:
        notes = list((await session.execute(sa.select(Notification).order_by(Notification.id))).scalars())
        sender_note = next(n for n in notes if n.recipient_id == users["prog"])
        assert "You shared 1 profile with Placement Team" in sender_note.title
        assert sender_note.notification_metadata["readiness_score"] == 9
        assert sender_note.notification_metadata["notes"] == "Shortlisted candidates with strong skills."
        assert sender_note.notification_metadata["candidate_ids"] == [a]
        assert sender_note.notification_metadata["target_queue"] == "Placement Team"
