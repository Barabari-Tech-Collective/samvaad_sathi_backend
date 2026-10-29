"""Local-only bootstrap: never loads a saved backend dotenv file."""
import json
import os
from pathlib import Path
import sys

state = Path(sys.argv[1])
settings = json.loads(state.read_text())
if settings.get("POSTGRES_HOST") != "127.0.0.1" or settings.get("POSTGRES_DB") != "samvaad_local" or settings.get("REDIS_PORT") != "16379":
    raise SystemExit("Dedicated local resources required")
os.environ.update(settings)
import decouple
import dotenv
from pydantic_settings.sources import DotEnvSettingsSource
decouple.config = decouple.Config({})
dotenv.load_dotenv = lambda *args, **kwargs: False
DotEnvSettingsSource._read_env_files = lambda self: {}
root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "backend/backend"))
mode = sys.argv[2]
if mode == "migrate":
    os.chdir(root / "backend/backend")
    from alembic.config import main
    main(["upgrade", "head"])
elif mode == "resume-operations":
    import asyncio
    from resume_operation_acceptance import main
    asyncio.run(main())
elif mode == "resume-http":
    import asyncio
    from resume_http_acceptance import main
    asyncio.run(main())
elif mode == "resume-crash-upload":
    import asyncio
    from object_storage_acceptance import crash_after_upload
    asyncio.run(crash_after_upload(int(sys.argv[3]), sys.argv[4]))
elif mode == "resume-objects":
    import asyncio
    from object_storage_acceptance import main
    asyncio.run(main())
elif mode == "resume-runtime":
    import asyncio
    from runtime_acceptance import main
    asyncio.run(main())
elif mode == "resume-storage":
    import asyncio
    from resume_storage_acceptance import main
    asyncio.run(main())
elif mode == "api":
    import uvicorn
    uvicorn.run("src.main:backend_app", host="127.0.0.1", port=8440 if settings.get("LOCAL_TLS_KEY") else 8000, ssl_keyfile=settings.get("LOCAL_TLS_KEY"), ssl_certfile=settings.get("LOCAL_TLS_CERT"))
elif mode == "worker":
    from arq import run_worker
    from src.worker.main import WorkerSettings
    run_worker(WorkerSettings)
elif mode == "storage":
    from src.services.s3_service import s3_client, BUCKET_NAME
    from botocore.exceptions import ClientError
    try:
        s3_client.head_bucket(Bucket=BUCKET_NAME)
    except ClientError as error:
        if str(error.response.get("Error", {}).get("Code")) not in {"404", "NoSuchBucket"}:
            raise
        s3_client.create_bucket(Bucket=BUCKET_NAME)
    # Only synthesized question audio is anonymously readable. All learner files stay private.
    s3_client.put_bucket_policy(Bucket=BUCKET_NAME, Policy=json.dumps({"Version":"2012-10-17", "Statement":[{"Effect":"Allow", "Principal":"*", "Action":"s3:GetObject", "Resource":f"arn:aws:s3:::{BUCKET_NAME}/Samvaad-Saathi/tts-audio/*"}]}))
else:
    raise SystemExit("Unknown local mode")
