"""Platform audit F17 and F18: resume scores were lost on the way to Sampark.

F18: with SAMPARK_SAATHI_* unset, every callback returned early with a log line.
F17: the worker's class-wide max_tries = 1 refused every retry the task asked for.
"""

import httpx
import pytest
from arq.worker import Retry

from src.services import barabari_integration as integration
from src.worker import main as worker_main
from src.worker import tasks as tasks_module


def _settings(monkeypatch, **values):
    for name, value in values.items():
        monkeypatch.setattr(integration.settings, name, value, raising=False)


def test_startup_refuses_missing_sampark_settings(monkeypatch):
    _settings(monkeypatch, SAMPARK_INTEGRATION_ENABLED=True, SAMPARK_SAATHI_BASE_URL="", SAMPARK_SAATHI_API_KEY="k")
    with pytest.raises(RuntimeError, match="SAMPARK_SAATHI_BASE_URL"):
        integration.verify_sampark_integration_configuration()


def test_startup_names_every_missing_setting(monkeypatch):
    _settings(monkeypatch, SAMPARK_INTEGRATION_ENABLED=True, SAMPARK_SAATHI_BASE_URL=" ", SAMPARK_SAATHI_API_KEY="")
    with pytest.raises(RuntimeError) as err:
        integration.verify_sampark_integration_configuration()
    assert "SAMPARK_SAATHI_BASE_URL" in str(err.value)
    assert "SAMPARK_SAATHI_API_KEY" in str(err.value)


def test_startup_passes_when_configured(monkeypatch):
    _settings(
        monkeypatch,
        SAMPARK_INTEGRATION_ENABLED=True,
        SAMPARK_SAATHI_BASE_URL="https://sampark.example",
        SAMPARK_SAATHI_API_KEY="k",
    )
    integration.verify_sampark_integration_configuration()


def test_switching_the_integration_off_is_explicit(monkeypatch):
    _settings(monkeypatch, SAMPARK_INTEGRATION_ENABLED=False, SAMPARK_SAATHI_BASE_URL="", SAMPARK_SAATHI_API_KEY="")
    integration.verify_sampark_integration_configuration()


@pytest.mark.asyncio
async def test_missing_settings_at_send_time_fail_instead_of_skipping(monkeypatch):
    _settings(monkeypatch, SAMPARK_INTEGRATION_ENABLED=True, SAMPARK_SAATHI_BASE_URL="", SAMPARK_SAATHI_API_KEY="")
    with pytest.raises(RuntimeError):
        await integration.submit_resume_score_to_barabari(student_id="S1", resume_score=70, request_id="r")


def _client_returning(monkeypatch, status_code):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            return httpx.Response(status_code, request=httpx.Request("POST", url), json={})

    monkeypatch.setattr(integration.httpx, "AsyncClient", FakeClient)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 404, 422])
async def test_a_4xx_is_permanent(monkeypatch, status):
    _settings(
        monkeypatch,
        SAMPARK_INTEGRATION_ENABLED=True,
        SAMPARK_SAATHI_BASE_URL="https://sampark.example",
        SAMPARK_SAATHI_API_KEY="k",
    )
    _client_returning(monkeypatch, status)
    with pytest.raises(integration.PermanentCallbackError):
        await integration.submit_resume_score_to_barabari(student_id="S1", resume_score=70, request_id="r")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 429, 500, 503])
async def test_a_temporary_failure_is_retried(monkeypatch, status):
    _settings(
        monkeypatch,
        SAMPARK_INTEGRATION_ENABLED=True,
        SAMPARK_SAATHI_BASE_URL="https://sampark.example",
        SAMPARK_SAATHI_API_KEY="k",
    )
    _client_returning(monkeypatch, status)
    with pytest.raises(Retry):
        await tasks_module.submit_resume_score_task(
            {"job_try": 1}, student_id="S1", resume_score=70, request_id="r"
        )


@pytest.mark.asyncio
async def test_task_stops_quietly_on_a_permanent_refusal(monkeypatch):
    async def refuse(**kwargs):
        raise integration.PermanentCallbackError("HTTP 404")

    monkeypatch.setattr(tasks_module, "submit_resume_score_to_barabari", refuse)
    assert await tasks_module.submit_resume_score_task({"job_try": 1}, student_id="S1", resume_score=70, request_id="r") is None


def test_callback_tasks_have_their_own_retry_limit():
    by_name = {}
    for f in worker_main.WorkerSettings.functions:
        name = getattr(f, "name", None) or getattr(f, "__name__", None)
        by_name[name] = getattr(f, "max_tries", None)
    assert by_name["submit_resume_score_task"] == 8
    assert by_name["submit_batch_choice_task"] == 8
    # Question generation still must not retry.
    assert worker_main.WorkerSettings.max_tries == 1


def test_worker_checks_configuration_at_startup():
    assert worker_main.WorkerSettings.on_startup is worker_main.verify_configuration


def test_sampark_callbacks_keep_their_retries_on_both_workers():
    """With WORKER_SLOW_QUEUE_ENABLED the callbacks run on the slow worker, whose class-wide
    max_tries is 1; each callback must still carry its own max_tries there."""
    from src.worker.main import SlowWorkerSettings, WorkerSettings

    def tries(settings_cls, name):
        for f in settings_cls.functions:
            if getattr(f, "name", getattr(f, "__name__", "")) == name:
                return getattr(f, "max_tries", None)
        return "missing"

    for cls in (WorkerSettings, SlowWorkerSettings):
        for name in ("submit_resume_score_task", "submit_batch_choice_task"):
            assert tries(cls, name) == 8, (cls.__name__, name)
