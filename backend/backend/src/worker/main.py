"""arq worker entrypoint. Run on the EC2 instance as a separate long-lived
process from the API server:

    arq src.worker.main.WorkerSettings

See deploy/samvaad-worker.service for a systemd unit that runs this.
"""

from src.services.resume_storage_delivery import deliver_resume_files
from arq import cron
from src.config.json_logging import configure_root_logging
from src.config.manager import settings as _settings
from arq.worker import func

from src.config.manager import settings
from src.services.barabari_integration import verify_sampark_integration_configuration
from src.worker.queue import SLOW_QUEUE
from src.worker.settings import redis_settings
from src.worker.tasks import (
    dispatch_notification_deliveries_task,
    generate_bulk_ats_feedback_task,
    generate_questions_task,
    process_answer_transcription_task,
    generate_summary_report_task,
    process_bulk_ats_candidate_task,
    purge_expired_bulk_ats_batches_task,
    refresh_dashboard_aggregate_task,
    sweep_stalled_answer_transcriptions_task,
    submit_batch_choice_task,
    submit_resume_score_task,
    synchronize_sampark_enrollments_task,
)

configure_root_logging(_settings.LOGGING_LEVEL)


async def verify_configuration(ctx: dict) -> None:
    """Refuse to start the worker when a configured integration cannot work."""
    verify_sampark_integration_configuration()


class WorkerSettings:
    on_startup = verify_configuration
    functions = [
        func(synchronize_sampark_enrollments_task,max_tries=1,timeout=300),
        # Callbacks to Sampark retry with a growing delay. Without their own max_tries the
        # class-wide max_tries = 1 below refused every retry, so a short Sampark outage lost
        # the resume score (platform audit F17).
        func(submit_resume_score_task, max_tries=8),
        func(submit_batch_choice_task, max_tries=8),
        generate_questions_task,
        func(generate_summary_report_task, max_tries=8, timeout=300),
        func(process_answer_transcription_task, max_tries=3, timeout=300),
        process_bulk_ats_candidate_task,
        generate_bulk_ats_feedback_task,
        purge_expired_bulk_ats_batches_task,
        func(refresh_dashboard_aggregate_task, max_tries=1, timeout=120),
    ]
    cron_jobs = [
        cron(dispatch_notification_deliveries_task, minute=set(range(60)), run_at_startup=False, timeout=120),
        cron(deliver_resume_files, minute=set(range(60)), run_at_startup=True, timeout=300),
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
    max_jobs = settings.WORKER_MAX_JOBS
    max_tries = (
        1  # question generation must not auto-retry (would create duplicate questions)
    )


class SlowWorkerSettings:
    """Second process for long jobs, used when WORKER_SLOW_QUEUE_ENABLED is on:

        arq src.worker.main.SlowWorkerSettings

    See deploy/samvaad-worker-slow.service. No cron jobs run here; the default
    worker keeps them.
    """

    queue_name = SLOW_QUEUE
    functions = [
        # Same retry budget as on the main worker: the class-wide max_tries = 1 would refuse
        # every retry, and a short Sampark outage would lose the score (platform audit F17).
        func(submit_resume_score_task, max_tries=8),
        func(submit_batch_choice_task, max_tries=8),
        generate_questions_task,
        func(generate_summary_report_task, max_tries=8, timeout=300),
        process_bulk_ats_candidate_task,
        generate_bulk_ats_feedback_task,
        func(refresh_dashboard_aggregate_task, max_tries=1, timeout=120),
    ]
    redis_settings = redis_settings
    max_jobs = settings.WORKER_SLOW_MAX_JOBS
    max_tries = 1
