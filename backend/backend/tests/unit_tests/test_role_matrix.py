"""Role matrix: the backend's /me answer and its menu decision for every staff role row.

tests/fixtures/role_matrix.json holds, per row, the /me payload and the menu sections the
backend decides. The dashboard repo keeps a byte-identical copy and checks that its own
reading of the same payload opens the same sections. Regenerate after an intended access
change with ROLE_MATRIX_WRITE=1, then copy the file to the dashboard repo.
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from role_matrix_rows import ROWS, me_payload  # noqa: E402
from src.services.workspace_access import workspace_sections  # noqa: E402

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "role_matrix.json"


def _current():
    rows = []
    for name in ROWS:
        payload = me_payload(name)
        rows.append({"name": name, "me": payload, "sections": workspace_sections(payload, None)})
    return {"rows": rows}


def test_role_matrix_matches_the_reviewed_fixture():
    current = _current()
    if os.environ.get("ROLE_MATRIX_WRITE") == "1":
        FIXTURE.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    assert current == json.loads(FIXTURE.read_text())


def test_every_role_row_opens_at_least_one_section_and_admins_open_all():
    for row in _current()["rows"]:
        assert row["sections"], row["name"]
    names = {r["name"]: set(r["sections"]) for r in _current()["rows"]}
    assert names["Admin"] == names["Super admin"]
    assert names["Program team member"] <= names["Program team lead"]


def test_me_sends_the_sections_for_staff_only():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from src.api.routes import users

    row_user, specs = ROWS["Program team lead"]
    from role_matrix_rows import _memberships, _user

    user = _user(row_user)
    for attr, value in {"created_at": __import__("datetime").datetime(2026, 1, 1), "is_onboarded": True,
                        "degree": None, "university": None, "target_position": None, "years_experience": None,
                        "resume_text": None, "skills": None, "company": None, "email": "lead@barabari.org"}.items():
        setattr(user, attr, value)
    summary = SimpleNamespace(count_by_user=AsyncMock(return_value=0))
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    with patch.object(users, "get_user_active_memberships", AsyncMock(return_value=_memberships(specs))):
        profile, _ = asyncio.run(users.build_user_profile(user, summary, session))
    expected = next(r for r in json.loads(FIXTURE.read_text())["rows"] if r["name"] == "Program team lead")
    assert profile.workspace_sections == expected["sections"]
    student = _user({})
    for attr in ("created_at", "is_onboarded", "degree", "university", "target_position", "years_experience",
                 "resume_text", "skills", "company", "email"):
        setattr(student, attr, getattr(user, attr))
    with patch.object(users, "get_user_active_memberships", AsyncMock(return_value=[])):
        profile, _ = asyncio.run(users.build_user_profile(student, summary, session))
    assert profile.workspace_sections is None
