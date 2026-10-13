"""A column added at startup must not make its migration fail.

Production cannot be migrated yet, so `initialize_db_tables` patches some
columns in with ALTER TABLE ... ADD COLUMN IF NOT EXISTS to keep the app
runnable. Those same columns also have migrations. Whichever mechanism runs
first, the other has to be a no-op.

`op.add_column` is not a no-op - it raises DuplicateColumn - so a migration
covering a patched column must use IF NOT EXISTS instead. This has already gone
wrong twice: interview.difficulty could never be migrated, and the
transcription_status migration would have aborted on any host that had already
booted the new code.

The check is deliberately narrow. It only looks at columns the startup patch
actually touches, so ordinary migrations are free to use op.add_column.
"""

import ast
import pathlib
import re

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]
EVENTS = BACKEND_ROOT / "src/repository/events.py"
VERSIONS = BACKEND_ROOT / "src/repository/migrations/versions"


def _patched_columns() -> set[tuple[str, str]]:
    """(table, column) pairs that startup adds with ADD COLUMN IF NOT EXISTS."""
    pattern = re.compile(
        r"ALTER TABLE\s+\"?(\w+)\"?\s+ADD COLUMN IF NOT EXISTS\s+(\w+)",
        re.IGNORECASE,
    )
    # Statements are split across adjacent string literals, so match the joined text.
    source = re.sub(r'"\s*\n\s*"', "", EVENTS.read_text())
    return {(m.group(1), m.group(2)) for m in pattern.finditer(source)}


def _unguarded_add_columns(path: pathlib.Path) -> list[tuple[str, str]]:
    """(table, column) pairs added via a bare, unconditional op.add_column.

    Calls sitting inside an `if` are treated as guarded: several migrations
    already consult the inspector and skip the column when it is present, which
    is correct and must not be reported.
    """
    tree = ast.parse(path.read_text())
    conditional = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            for descendant in ast.walk(node):
                conditional.add(id(descendant))

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in conditional:
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_column"):
            continue
        if not node.args:
            continue
        table = node.args[0]
        if not (isinstance(table, ast.Constant) and isinstance(table.value, str)):
            continue
        for argument in node.args[1:]:
            if (
                isinstance(argument, ast.Call)
                and argument.args
                and isinstance(argument.args[0], ast.Constant)
            ):
                found.append((table.value, argument.args[0].value))
    return found


def test_startup_actually_patches_some_columns():
    """Guards the guard: a regex that matched nothing would pass everything."""
    patched = _patched_columns()
    assert patched, f"No ADD COLUMN IF NOT EXISTS statements found in {EVENTS}"
    assert ("question_attempt", "transcription_status") in patched


@pytest.mark.parametrize(
    "path", sorted(VERSIONS.glob("*.py")), ids=lambda p: p.name[:40]
)
def test_migration_does_not_readd_a_startup_patched_column(path: pathlib.Path):
    patched = _patched_columns()
    collisions = [pair for pair in _unguarded_add_columns(path) if pair in patched]
    assert not collisions, (
        f"{path.name} uses op.add_column for "
        + ", ".join(f"{t}.{c}" for t, c in collisions)
        + ", which startup also adds. Whichever runs first, the other breaks: "
        "op.add_column raises DuplicateColumn. Use "
        "op.execute('ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...') instead."
    )
