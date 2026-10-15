"""Reject destructive Alembic upgrade operations from the automatic CD path.

Production application rollback restores the previous commit but deliberately
does not downgrade the database. Therefore every automatically deployed schema
change must use expand/contract: additive changes first, application rollout
second, and destructive cleanup only in a separately reviewed maintenance
release.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION_ROOT = ROOT / "backend/backend/src/repository/migrations/versions"
DESTRUCTIVE_OPERATIONS = {
    "alter_column",
    "drop_column",
    "drop_constraint",
    "drop_index",
    "drop_table",
    "rename_table",
}
DESTRUCTIVE_SQL = {"ALTER", "DELETE", "DROP", "TRUNCATE"}


def unsafe_upgrade_operations(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    upgrade = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "upgrade"
        ),
        None,
    )
    if upgrade is None:
        return ["missing upgrade()"]

    found: set[str] = set()
    for node in ast.walk(upgrade):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if (
            isinstance(owner, ast.Name)
            and owner.id == "op"
            and node.func.attr in DESTRUCTIVE_OPERATIONS
        ):
            found.add(f"op.{node.func.attr}")
        if (
            isinstance(owner, ast.Name)
            and owner.id == "op"
            and node.func.attr == "execute"
        ):
            statement = node.args[0] if node.args else None
            if not isinstance(statement, ast.Constant) or not isinstance(
                statement.value, str
            ):
                found.add("op.execute(dynamic SQL)")
                continue
            first_word = statement.value.lstrip().split(maxsplit=1)[0].upper()
            if first_word in DESTRUCTIVE_SQL:
                found.add(f"op.execute({first_word})")
    return sorted(found)


def changed_migrations(base: str, head: str) -> list[pathlib.Path]:
    try:
        if not base or base == head or set(base) == {"0"}:
            raise subprocess.CalledProcessError(1, "invalid event base")
        output = subprocess.check_output(
            [
                "git",
                "diff",
                "--name-only",
                "--diff-filter=AM",
                base,
                head,
                "--",
                str(MIGRATION_ROOT.relative_to(ROOT)),
            ],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        # workflow_call and the first push of a branch may not expose a usable
        # event base SHA. HEAD^ still checks the release commit rather than
        # silently disabling the gate.
        output = subprocess.check_output(
            [
                "git",
                "diff",
                "--name-only",
                "--diff-filter=AM",
                f"{head}^",
                head,
                "--",
                str(MIGRATION_ROOT.relative_to(ROOT)),
            ],
            cwd=ROOT,
            text=True,
        )
    return [ROOT / line for line in output.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", help="Git base SHA")
    parser.add_argument("head", help="Git head SHA")
    arguments = parser.parse_args()

    violations: list[tuple[pathlib.Path, list[str]]] = []
    migrations = changed_migrations(arguments.base, arguments.head)
    for path in migrations:
        unsafe = unsafe_upgrade_operations(path)
        if unsafe:
            violations.append((path, unsafe))

    if violations:
        print("Unsafe automatic migration detected:")
        for path, operations in violations:
            print(f"  {path.relative_to(ROOT)}: {', '.join(operations)}")
        print()
        print("Use an expand/contract migration, or run the destructive change as a")
        print("separately reviewed maintenance operation with a tested restore plan.")
        return 1

    if migrations:
        print(f"Migration safety passed for {len(migrations)} changed revision(s).")
    else:
        print("No migration revisions changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
