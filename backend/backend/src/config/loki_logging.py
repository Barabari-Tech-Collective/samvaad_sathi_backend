import atexit
import logging
import queue

import logging_loki

from src.config.manager import settings

# Super-admin plan, Phase 13: attaches to the root logger (already centrally configured via
# logging.basicConfig() in main.py), so every module using logging.getLogger(__name__) fans
# out to Grafana Cloud automatically - no per-call-site changes needed, unlike auth-service
# which had no logging library at all to hook into. Deliberately does not touch the loguru
# call sites (a handful of infra files) or stray print() statements elsewhere in the
# codebase - those remain console-only for now; the stdlib `logging` root handler here already
# captures the overwhelming majority of application logs.
def configure_loki_logging() -> None:
    if not settings.LOKI_URL:
        return

    auth = (
        (settings.LOKI_USERNAME, settings.LOKI_API_KEY)
        if settings.LOKI_USERNAME and settings.LOKI_API_KEY
        else None
    )

    # LokiQueueHandler hands log records to a background thread (via logging.handlers.QueueListener)
    # instead of making an HTTP request inline on the thread that emitted the log - a request
    # handler logging a line should never block on Loki being slow or unreachable.
    log_queue: "queue.Queue" = queue.Queue(-1)
    handler = logging_loki.LokiQueueHandler(
        log_queue,
        url=settings.LOKI_URL,
        auth=auth,
        tags={"service": "samvaad-saathi-backend", "environment": settings.ENVIRONMENT},
        version="1",
    )

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    atexit.register(handler.listener.stop)
