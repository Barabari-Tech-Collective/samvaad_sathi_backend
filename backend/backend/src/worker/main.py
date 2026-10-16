"""arq worker entrypoint. Run on the EC2 instance as a separate long-lived
process from the API server:

    arq src.worker.main.WorkerSettings

See deploy/samvaad-worker.service for a systemd unit that runs this.
"""

from arq import cron

from src.worker.settings import redis_settings
from src.worker.tasks import (
    generate_bulk_ats_feedback_task,
    generate_questions_task,
    submit_resume_score_task,
    process_bulk_ats_candidate_task,
    purge_expired_bulk_ats_batches_task,
)


class WorkerSettings:
    functions = [
        submit_resume_score_task,
        generate_questions_task,
        process_bulk_ats_candidate_task,
        generate_bulk_ats_feedback_task,
        purge_expired_bulk_ats_batches_task,
    ]
    cron_jobs = [
        cron(
            purge_expired_bulk_ats_batches_task, hour=2, minute=15, run_at_startup=False
        )
    ]
    redis_settings = redis_settings
    max_tries = (
        1  # question generation must not auto-retry (would create duplicate questions)
    )
