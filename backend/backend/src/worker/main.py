"""arq worker entrypoint. Run on the EC2 instance as a separate long-lived
process from the API server:

    arq src.worker.main.WorkerSettings

See deploy/samvaad-worker.service for a systemd unit that runs this.
"""

from arq import cron
from arq.worker import func

from src.worker.settings import redis_settings
from src.worker.tasks import (
    generate_bulk_ats_feedback_task,
    generate_questions_task,
    process_answer_transcription_task,
    process_bulk_ats_candidate_task,
    purge_expired_bulk_ats_batches_task,
    sweep_stalled_answer_transcriptions_task,
    submit_resume_score_task,
    synchronize_sampark_enrollments_task,
)


class WorkerSettings:
    functions = [
        func(synchronize_sampark_enrollments_task,max_tries=1,timeout=300),
        submit_resume_score_task,
        generate_questions_task,
        func(process_answer_transcription_task, max_tries=3, timeout=300),
        process_bulk_ats_candidate_task,
        generate_bulk_ats_feedback_task,
        purge_expired_bulk_ats_batches_task,
    ]
    cron_jobs = [
        cron(synchronize_sampark_enrollments_task,minute=set(range(0,60,5)),run_at_startup=False),
        cron(
            purge_expired_bulk_ats_batches_task, hour=2, minute=15, run_at_startup=False
        ),
        cron(
            sweep_stalled_answer_transcriptions_task,
            minute={0, 10, 20, 30, 40, 50},
            run_at_startup=False,
        ),
    ]
    redis_settings = redis_settings
    max_tries = (
        1  # question generation must not auto-retry (would create duplicate questions)
    )
