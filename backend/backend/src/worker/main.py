"""arq worker entrypoint. Run on the EC2 instance as a separate long-lived
process from the API server:

    arq src.worker.main.WorkerSettings

See deploy/samvaad-worker.service for a systemd unit that runs this.
"""

from src.worker.settings import redis_settings
from src.worker.tasks import submit_resume_score_task


class WorkerSettings:
    functions = [submit_resume_score_task]
    redis_settings = redis_settings
    max_tries = 3
