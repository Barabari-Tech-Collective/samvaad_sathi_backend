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
import ast

import pytest

ROUTES_DIR = pathlib.Path(__file__).resolve().parents[2] / "src" / "api" / "routes"

# Matches a direct read of the is_admin attribute, e.g.
#   getattr(current_user, "is_admin", False)
#   current_user.is_admin
# The admin dependency module is exempt: it is where the real check lives.
def _raw_admin_reads(source: str) -> list[int]:
    """Inspect executable reads, not docstrings or an admin-update request field."""
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr == "is_admin":
            # Exempt only an exact `payload.is_admin` request field or the `User`
            # model column. Anything else - including a nested read such as
            # `request.state.user.is_admin` - must go through is_admin_user().
            base = node.value
            if not (isinstance(base, ast.Name) and base.id in {"payload", "User"}):
                offenders.append(node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "getattr" and len(node.args) >= 2:
                attribute = node.args[1]
                if isinstance(attribute, ast.Constant) and attribute.value == "is_admin":
                    offenders.append(node.lineno)
    return offenders


_EXEMPT = {
    "__init__.py",
    # internal_admin.py *sets* is_admin (the bridge that auth-service calls to
    # grant/revoke admin status). It legitimately passes payload.is_admin to
    # set_admin_status() — that is not an auth check; it is a write path.
    "internal_admin.py",
}


def _route_files() -> list[pathlib.Path]:
    return sorted(p for p in ROUTES_DIR.glob("*.py") if p.name not in _EXEMPT)


def test_route_files_exist():
    """Guards the glob itself - a wrong path would make this suite vacuous."""
    assert _route_files(), f"no route files found under {ROUTES_DIR}"


@pytest.mark.parametrize("path", _route_files(), ids=lambda p: p.name)
def test_no_raw_is_admin_checks_in_routes(path: pathlib.Path):
    offenders = [f"{path.name}:{lineno}" for lineno in _raw_admin_reads(path.read_text())]
    assert not offenders, (
        "Use is_admin_user() from api.dependencies.admin instead of reading "
        "is_admin directly, so ADMIN_EMAILS is honoured too:\n  "
        + "\n  ".join(offenders)
    )


def test_admin_guard_ignores_documentation_but_detects_executable_checks():
    assert _raw_admin_reads('"""User.is_admin"""\nupdate(is_admin=payload.is_admin)') == []
    assert _raw_admin_reads('if current_user.is_admin:\n    pass') == [1]
    assert _raw_admin_reads('getattr(current_user, "is_admin", False)') == [1]
    # A nested read must not become a blind spot just because the base is not a bare name.
    assert _raw_admin_reads('if request.state.user.is_admin:\n    pass') == [1]
