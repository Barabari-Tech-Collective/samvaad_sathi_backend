"""Every admin check must go through is_admin_user().

Two routes in analytics.py originally inlined
`getattr(current_user, "is_admin", False)` instead of using the shared
helper. When ADMIN_EMAILS was introduced as a second way to grant access,
those two silently kept honouring only the database column - so an
operator authorised by env var could use most of the dashboard but would
still get 403 on /analytics/student/{id} and /analytics/interview/{id}.

That is the kind of split-brain authorization bug that is invisible until
someone hits the one endpoint that disagrees, so this asserts the raw
pattern does not come back.
"""

import pathlib
import re

import pytest

ROUTES_DIR = pathlib.Path(__file__).resolve().parents[2] / "src" / "api" / "routes"

# Matches a direct read of the is_admin attribute, e.g.
#   getattr(current_user, "is_admin", False)
#   current_user.is_admin
# The admin dependency module is exempt: it is where the real check lives.
_RAW_IS_ADMIN = re.compile(
    r"""getattr\(\s*\w+\s*,\s*["']is_admin["']|\b\w+\.is_admin\b"""
)


def _route_files() -> list[pathlib.Path]:
    return sorted(p for p in ROUTES_DIR.glob("*.py") if p.name != "__init__.py")


def test_route_files_exist():
    """Guards the glob itself - a wrong path would make this suite vacuous."""
    assert _route_files(), f"no route files found under {ROUTES_DIR}"


@pytest.mark.parametrize("path", _route_files(), ids=lambda p: p.name)
def test_no_raw_is_admin_checks_in_routes(path: pathlib.Path):
    offenders = [
        f"{path.name}:{lineno}: {line.strip()}"
        for lineno, line in enumerate(path.read_text().splitlines(), start=1)
        if _RAW_IS_ADMIN.search(line)
    ]
    assert not offenders, (
        "Use is_admin_user() from api.dependencies.admin instead of reading "
        "is_admin directly, so ADMIN_EMAILS is honoured too:\n  "
        + "\n  ".join(offenders)
    )
