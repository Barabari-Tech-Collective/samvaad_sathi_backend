"""Operational scripts must not drift away from the settings they read.

ops/ is outside the application package and therefore outside the rest of the
suite, so a renamed setting breaks these scripts silently and only shows up when
someone reaches for one - usually while already dealing with an incident. This
caught dev_login_token.py reading POSTGRES_HOST, which is the .env key name and
not the attribute (DB_POSTGRES_HOST) pydantic actually exposes.
"""

import ast
import pathlib

import pytest

from src.config.manager import settings

# tests/unit_tests -> tests -> backend/backend -> backend -> repository root
OPS_DIR = pathlib.Path(__file__).resolve().parents[4] / "ops"


def _settings_attributes(path: pathlib.Path) -> set[str]:
    """Collects every application-settings attribute a script reads.

    Only scripts importing settings from src.config.manager are inspected, and
    only upper-case names are treated as settings: release.py holds an unrelated
    API response in a local called `settings` and calls .get() on it.
    """
    tree = ast.parse(path.read_text())
    imports_settings = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "src.config.manager"
        and any(alias.name == "settings" for alias in node.names)
        for node in ast.walk(tree)
    )
    if not imports_settings:
        return set()
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "settings"
        and node.attr.isupper()
    }


def _ops_scripts() -> list[pathlib.Path]:
    return sorted(OPS_DIR.glob("*.py"))


def test_ops_directory_is_present():
    assert _ops_scripts(), f"No ops scripts found under {OPS_DIR}"


def test_detector_finds_real_reads_and_ignores_unrelated_locals(tmp_path):
    """Proves the check can still fail, so a green run means something."""
    real = tmp_path / "real.py"
    real.write_text("from src.config.manager import settings\nprint(settings.DB_POSTGRES_HOST)\n")
    assert _settings_attributes(real) == {"DB_POSTGRES_HOST"}

    unrelated = tmp_path / "unrelated.py"
    unrelated.write_text("settings = api()\nif settings.get('autoDeploy'):\n    pass\n")
    assert _settings_attributes(unrelated) == set()


@pytest.mark.parametrize("path", _ops_scripts(), ids=lambda p: p.name)
def test_ops_script_reads_only_real_settings(path: pathlib.Path):
    missing = sorted(
        attribute for attribute in _settings_attributes(path) if not hasattr(settings, attribute)
    )
    assert not missing, (
        f"{path.name} reads settings that do not exist: {', '.join(missing)}. "
        "Note the .env key and the settings attribute differ, e.g. POSTGRES_HOST "
        "is exposed as DB_POSTGRES_HOST."
    )
