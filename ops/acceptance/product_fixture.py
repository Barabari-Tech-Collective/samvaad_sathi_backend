"""Local acceptance-only entrypoint; excludes every saved .env file."""

import os
from pathlib import Path
import re
import sys

if (
    os.environ.get("POSTGRES_HOST") != "127.0.0.1"
    or not re.fullmatch(r"ss_acceptance_[a-z0-9_]+", os.environ.get("POSTGRES_DB", ""))
    or os.environ.get("POSTGRES_USERNAME") != "ss_acceptance"
):
    raise SystemExit("Disposable local acceptance database required")

# Configuration isolation only; actual services/routes/repositories stay intact.
import decouple
from pydantic_settings.sources import DotEnvSettingsSource

decouple.config = decouple.Config({})
DotEnvSettingsSource._read_env_files = lambda self: {}
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/backend"))

if sys.argv[1] == "migrate":
    from alembic.config import main

    main(["upgrade", "head"])
elif sys.argv[1] == "sync":
    import asyncio
    from src.repository.database import async_db
    from src.services.sampark_enrollment_sync import synchronize

    async def run():
        async with async_db.get_session() as session:
            result = await synchronize(session)
            if result.get("status") != "complete":
                raise RuntimeError("Fixture sync did not complete")
        await async_db.async_engine.dispose()

    asyncio.run(run())
elif sys.argv[1] == "serve":
    import uvicorn

    uvicorn.run(
        "src.main:backend_app",
        host="127.0.0.1",
        port=int(os.environ["BACKEND_SERVER_PORT"]),
        ssl_keyfile=os.environ["ACCEPTANCE_TLS_KEY"],
        ssl_certfile=os.environ["ACCEPTANCE_TLS_CERT"],
    )
else:
    raise SystemExit("Expected migrate, sync or serve")
