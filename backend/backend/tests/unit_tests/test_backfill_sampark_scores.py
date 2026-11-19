import asyncio

import pytest

from src.commands.backfill_sampark_scores import LatestScore, latest_scores, read_ids, run
from src.services.barabari_integration import PermanentCallbackError


def test_read_ids_accepts_the_sql_csv_and_plain_lists(tmp_path):
    csv_file = tmp_path / "m.csv"
    csv_file.write_text("student_unique_id,registered_on\nS1,2026-07-01\nS2,2026-07-02\nS1,2026-07-03\n")
    assert read_ids(csv_file) == ["S1", "S2"]
    plain = tmp_path / "m.txt"
    plain.write_text("S3\n\nS4\n")
    assert read_ids(plain) == ["S3", "S4"]


def test_latest_analysis_wins_and_rows_without_a_score_are_ignored():
    rows = [
        (1, "S1", {"atsScore": 40}, "SDE"),
        (5, "S1", {"atsScore": 71.6}, "Data"),
        (3, "S2", {}, None),
        (4, None, {"atsScore": 90}, None),
    ]
    out = latest_scores(rows)
    assert out == {"S1": LatestScore("S1", 72, "Data")}


def test_dry_run_counts_and_sends_nothing():
    sent = []

    async def post(item, rid):
        sent.append(item)

    report = asyncio.run(run(["S1", "S2"], {"S1": LatestScore("S1", 70, None)}, post, dry_run=True))
    assert (report.requested, report.no_analysis, report.to_send) == (2, 1, 1)
    assert sent == []


def test_real_run_counts_stored_refused_and_failed():
    async def post(item, rid):
        assert rid.startswith("backfill-")
        if item.student_id == "R":
            raise PermanentCallbackError("404")
        if item.student_id == "F":
            raise RuntimeError("down")

    scores = {i: LatestScore(i, 50, None) for i in ["OK", "R", "F"]}
    report = asyncio.run(run(["OK", "R", "F"], scores, post, dry_run=False, per_second=0))
    assert (report.sent, report.refused, report.failed) == (1, 1, 1)
    assert report.failures == ["F"]


@pytest.mark.parametrize("backfill,expected", [(True, "true"), (False, None)])
def test_backfill_flag_sets_the_header(monkeypatch, backfill, expected):
    import httpx

    from src.services import barabari_integration as bi

    seen = {}

    class FakeClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            seen.update(headers)
            return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(bi.settings, "SAMPARK_SAATHI_API_KEY", "k", raising=False)
    monkeypatch.setattr(bi.settings, "SAMPARK_SAATHI_BASE_URL", "http://sampark", raising=False)
    monkeypatch.setattr(bi.httpx, "AsyncClient", FakeClient)
    asyncio.run(bi.submit_resume_score_to_barabari("S1", 60, "r", backfill=backfill))
    assert seen.get("X-Backfill") == expected
