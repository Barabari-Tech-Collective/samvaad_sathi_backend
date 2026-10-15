import importlib.util
import pathlib

SCRIPT_PATH = (
    pathlib.Path(__file__).resolve().parents[4] / "ops/check_migration_safety.py"
)
spec = importlib.util.spec_from_file_location("migration_safety", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
migration_safety = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration_safety)
unsafe_upgrade_operations = migration_safety.unsafe_upgrade_operations


def migration(tmp_path: pathlib.Path, upgrade_body: str, downgrade_body: str = "pass"):
    path = tmp_path / "revision.py"
    path.write_text(
        "from alembic import op\n\n"
        "def upgrade():\n"
        f"    {upgrade_body}\n\n"
        "def downgrade():\n"
        f"    {downgrade_body}\n"
    )
    return path


def test_additive_upgrade_is_accepted(tmp_path):
    path = migration(
        tmp_path,
        'op.add_column("interview", sa.Column("safe", sa.String()))',
        'op.drop_column("interview", "safe")',
    )

    assert unsafe_upgrade_operations(path) == []


def test_downgrade_drops_do_not_block_an_additive_release(tmp_path):
    path = migration(
        tmp_path,
        'op.create_index("ix_safe", "interview", ["safe"])',
        'op.drop_index("ix_safe")',
    )

    assert unsafe_upgrade_operations(path) == []


def test_destructive_upgrade_is_rejected(tmp_path):
    path = migration(tmp_path, 'op.drop_column("interview", "student_answer")')

    assert unsafe_upgrade_operations(path) == ["op.drop_column"]


def test_raw_sql_upgrade_requires_a_maintenance_release(tmp_path):
    path = migration(tmp_path, 'op.execute("DELETE FROM interview")')

    assert unsafe_upgrade_operations(path) == ["op.execute(DELETE)"]


def test_data_backfill_is_allowed_in_an_expand_migration(tmp_path):
    path = migration(tmp_path, "op.execute(\"UPDATE interview SET status = 'active'\")")

    assert unsafe_upgrade_operations(path) == []


def test_dynamic_sql_requires_separate_review(tmp_path):
    path = migration(tmp_path, "op.execute(build_statement())")

    assert unsafe_upgrade_operations(path) == ["op.execute(dynamic SQL)"]


def test_missing_upgrade_is_rejected(tmp_path):
    path = tmp_path / "revision.py"
    path.write_text("def downgrade():\n    pass\n")

    assert unsafe_upgrade_operations(path) == ["missing upgrade()"]
