from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from finplat import jobs
from finplat.checks import run_checks
from finplat.db import session_scope
from finplat.models import JobRun


def _client():
    from finplat.api.main import app

    return TestClient(app)


def test_api_after_seed():
    jobs.seed_demo()
    with _client() as c:
        assert c.get("/symbols", params={"q": "hòa"}).json()[0]["ticker"] == "HPG"
        assert c.get("/symbols/xyz/prices").status_code == 404

        p = c.get("/symbols/vnm/prices", params={"limit": 30}).json()
        assert len(p["prices"]) == 30 and p["prices"][0]["date"] < p["prices"][-1]["date"]
        assert p["prices"][-1]["close"] > 1000  # đã quy về VND

        f = c.get("/symbols/FPT/financials", params={"periods": 4}).json()
        assert len(f["periods"]) == 4 and all(p.endswith(("Q1", "Q2", "Q3", "Q4")) for p in f["periods"])
        rev = [i for i in f["items"] if i["item_code"] == "revenue"]
        assert rev and all(i["yoy_pct"] is not None for i in rev)
        assert f["ratios"]

        n = c.get("/symbols/VNM/news").json()["news"]
        assert len(n) == 3 and n[0]["published_at"] >= n[-1]["published_at"]

        st = c.get("/status").json()
        assert {s["dataset"] for s in st["sources"]} >= {"prices", "news"}
        assert st["last_runs"][0]["status"] == "success"


def test_job_runs_logged_and_failures_reported(monkeypatch):
    def broken(*a, **k):
        raise ConnectionError("không kết nối được")

    from finplat.sources.vnstock_source import VnstockSource

    monkeypatch.setattr(VnstockSource, "fetch", broken)
    jobs.job_symbols_events(tickers=["VNM"])
    with session_scope() as s:
        run = s.scalars(select(JobRun).where(JobRun.job_name == "symbols_events")).one()
        assert run.status == "failed" and "không kết nối được" in run.message

    report = run_checks(now_vn=datetime(2026, 10, 2, 19, 0), notify=False)
    assert any("symbols_events" in p for p in report.problems)


def test_prices_skipped_on_holiday(monkeypatch):
    from datetime import date

    monkeypatch.setattr(jobs, "today_vn", lambda: date(2026, 9, 2))  # Quốc khánh
    jobs.job_prices_eod()
    with session_scope() as s:
        assert s.scalars(select(JobRun.status)).one() == "skipped"


def test_scheduler_builds_all_jobs():
    from finplat.scheduler import build_scheduler

    sched = build_scheduler()
    assert {j.id for j in sched.get_jobs()} == set(jobs.JOBS)
