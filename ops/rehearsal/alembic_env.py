"""Run alembic with the synthetic CI settings plus the rehearsal database from the env."""
import os
import pathlib
import sys

rehearsal = {k: os.environ[k] for k in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USERNAME", "POSTGRES_PASSWORD")}
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import ci_tests  # noqa: E402,F401  (synthetic settings)

os.environ.update(rehearsal | {"IS_DB_FORCE_ROLLBACK": "False"})
from alembic.config import main  # noqa: E402

main(argv=sys.argv[1:])
