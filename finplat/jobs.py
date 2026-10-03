"""Các job hằng ngày. Mỗi job ghi trạng thái vào bảng job_runs.

Luồng chung của job crawl: fetch -> lưu raw (commit) -> xử lý batch vừa lưu (nếu process_inline).
Xử lý lỗi thì batch ở trạng thái failed và job 18:30 sẽ chạy lại từ raw.

Đa luồng: các nguồn trong một job được fetch song song (source_workers), bên trong mỗi nguồn
lại song song theo mã/bài (crawl_workers, có giãn cách request_delay). Ghi raw và xử lý vào DB
thì tuần tự theo thứ tự nguồn, để không tranh chấp ghi và kết quả ổn định.
"""
from __future__ import annotations

import logging
import traceback
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable, Iterator

from sqlalchemy import or_, select

from .config import get_settings
from .db import init_db, session_scope
from .models import JobRun, News, Symbol
from .processing.normalize import url_hash
from .processing.pipeline import process_pending, tag_news
from .raw_store import RawStore
from .sources import get_source
from .trading_calendar import in_financial_season, is_trading_day, today_vn
from .utils import thread_map, utcnow

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ job_runs
@dataclass
class RunCtx:
    id: int
    records: int = 0
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: str | None = None


@contextmanager
def job_run(name: str) -> Iterator[RunCtx]:
    with session_scope() as s:
        run = JobRun(job_name=name, started_at=utcnow(), status="running")
        s.add(run)
        s.flush()
        ctx = RunCtx(id=run.id)
    status, err = "success", None
    try:
        yield ctx
        if ctx.skipped:
            status = "skipped"
        elif ctx.errors:
            status = "partial" if ctx.records else "failed"
    except Exception:  # noqa: BLE001
        status, err = "failed", traceback.format_exc()[-8000:]
        log.exception("Job %s lỗi", name)
    finally:
        with session_scope() as s:
            run = s.get(JobRun, ctx.id)
            run.finished_at = utcnow()
            run.status = status
            run.records = ctx.records
            msg = ([ctx.skipped] if ctx.skipped else []) + ctx.notes + [f"LỖI: {e}" for e in ctx.errors]
            run.message = "\n".join(msg)[:8000] or None
            run.error = err or ("\n".join(ctx.errors)[:8000] if ctx.errors else None)
    if status == "failed" and err:
        raise RuntimeError(f"Job {name} lỗi")


def last_run_status(job_name: str) -> str | None:
    with session_scope() as s:
        return s.scalar(select(JobRun.status).where(JobRun.job_name == job_name).order_by(JobRun.id.desc()).limit(1))


CrawlTask = tuple[str, str, dict]  # (source, dataset, params)


def crawl(ctx: RunCtx, source_name: str, dataset: str, **params) -> None:
    """Fetch một nguồn -> lưu raw -> (tuỳ chọn) xử lý ngay. Lỗi nguồn ghi vào ctx, không làm dừng job."""
    crawl_many(ctx, [(source_name, dataset, params)])


def crawl_many(ctx: RunCtx, tasks: list[CrawlTask]) -> None:
    """Fetch nhiều (nguồn, dataset) song song, rồi lưu raw + xử lý tuần tự theo thứ tự tasks."""
    if not tasks:
        return

    def fetch(task: CrawlTask):
        source_name, dataset, params = task
        try:
            return get_source(source_name).fetch(dataset, **params)
        except Exception as e:  # noqa: BLE001
            log.exception("%s/%s fetch lỗi", source_name, dataset)
            return e

    workers = int(get_settings().general("source_workers", 4))
    for (source_name, dataset, params), records in zip(tasks, thread_map(fetch, tasks, workers)):
        if isinstance(records, Exception):
            ctx.errors.append(f"{source_name}/{dataset}: {type(records).__name__}: {records}")
            continue
        _store_and_process(ctx, source_name, dataset, params, records)


def _store_and_process(ctx: RunCtx, source_name: str, dataset: str, params: dict, records: list[dict]) -> None:
    saved_params = {k: v for k, v in params.items() if not callable(v)}
    requested = None
    if "tickers" in saved_params:
        requested = len(saved_params["tickers"])
        saved_params["tickers"] = requested  # không lưu danh sách dài
    bad = [r for r in records if "_error" in r]
    good = [r for r in records if "_error" not in r]
    # Độ phủ: theo mã nếu crawl theo mã (một mã có thể nhiều dòng), còn lại theo bản ghi
    ok = len({r.get("ticker") for r in good}) if requested is not None else len(good)
    with session_scope() as s:
        batch = RawStore().save(s, source_name, dataset, records, saved_params, job_run_id=ctx.id)
        batch.requested, batch.ok_count, batch.error_count = requested, ok, len(bad)
        batch_id = batch.id
    cov = f"{ok}/{requested} mã" if requested is not None else f"{len(good)} bản ghi raw"
    ctx.notes.append(f"{source_name}/{dataset}: {cov}" + (f", {len(bad)} lỗi" if bad else ""))
    if bad:
        sample = "; ".join(f"{r.get('ticker') or r.get('feed_url')}: {r['_error']}" for r in bad[:5])
        ctx.errors.append(f"{source_name}/{dataset}: {len(bad)} mục lỗi ({sample})")
    if get_settings().general("process_inline", True):
        res = process_pending(batch_ids=[batch_id])
        ctx.records += res["records"]
        ctx.errors.extend(res["errors"])
    else:
        ctx.records += len(good)


def active_tickers(exchanges: list[str] | None = None) -> list[str]:
    exchanges = exchanges or get_settings().general("exchanges", ["HOSE", "HNX", "UPCOM"])
    with session_scope() as s:
        q = select(Symbol.ticker).where(
            Symbol.is_active.is_(True),
            Symbol.exchange.in_(exchanges),
            or_(Symbol.type.is_(None), Symbol.type.in_(["STOCK", "stock"])),
        ).order_by(Symbol.ticker)
        return list(s.scalars(q))


def rotating_subset(tickers: list[str]) -> list[str]:
    """Ngoài mùa BCTC: mỗi ngày 1/N số mã (theo hash ổn định) + watchlist."""
    fs = get_settings().financial_season
    n = int(fs.get("rotation_days", 7))
    slot = today_vn().toordinal() % n
    watch = set(fs.get("watchlist", []))
    return [t for t in tickers if t in watch or zlib.crc32(t.encode()) % n == slot]


# ------------------------------------------------------------------ jobs
def job_symbols_events(tickers: list[str] | None = None) -> None:
    s = get_settings()
    with job_run("symbols_events") as ctx:
        for src in s.enabled_sources("symbols"):
            crawl(ctx, src, "symbols")
        universe = tickers or rotating_subset(active_tickers())
        crawl_many(ctx, [(src, "events", {"tickers": universe}) for src in s.enabled_sources("events")])


def job_news() -> None:
    s = get_settings()
    with job_run("news") as ctx:
        cutoff = utcnow() - timedelta(days=3)
        with session_scope() as sess:
            known = set(sess.scalars(select(News.id).where(News.created_at >= cutoff)))
        skip: Callable[[str], bool] = lambda url: url_hash(url) in known  # noqa: E731
        crawl_many(ctx, [(src, "news", {"skip": skip}) for src in s.enabled_sources("news")]
                   + [(src, "disclosures", {}) for src in s.enabled_sources("disclosures")])
        if s.general("process_inline", True):
            ctx.notes.append(f"gắn mã: {tag_news()} liên kết")


def job_prices_eod(tickers: list[str] | None = None, force: bool = False) -> None:
    s = get_settings()
    with job_run("prices_eod") as ctx:
        today = today_vn()
        if not force and not is_trading_day(today):
            ctx.skipped = f"{today} không phải ngày giao dịch"
            return
        universe = tickers or active_tickers()
        if not universe:
            ctx.errors.append("Chưa có danh sách mã, chạy job symbols_events trước")
            return
        start = today - timedelta(days=int(s.general("price_lookback_days", 7)))
        crawl_many(ctx, [(src, "prices", {"tickers": universe, "start": start.isoformat(), "end": today.isoformat()})
                         for src in s.enabled_sources("prices")])


def job_financials(tickers: list[str] | None = None, full: bool | None = None) -> None:
    s = get_settings()
    with job_run("financials") as ctx:
        all_t = active_tickers()
        full = in_financial_season(today_vn()) if full is None else full
        universe = tickers or (all_t if full else rotating_subset(all_t))
        ctx.notes.append(f"{'mùa BCTC: toàn bộ' if full else 'ngoài mùa: xoay vòng'} {len(universe)} mã")
        crawl_many(ctx, [(src, "financials", {"tickers": universe}) for src in s.enabled_sources("financials")]
                   + [(src, "ratios", {"tickers": universe}) for src in s.enabled_sources("ratios")])


def job_processing() -> None:
    with job_run("processing") as ctx:
        res = process_pending()
        ctx.records = res["records"]
        ctx.errors.extend(res["errors"])
        tagged = tag_news()
        ctx.notes.append(f"{res['batches']} batch xử lý, {res['failed']} lỗi; gắn mã {tagged} liên kết")


def job_checks() -> None:
    from .checks import run_checks

    with job_run("checks") as ctx:
        report = run_checks()
        ctx.notes.append(report.summary())
        ctx.records = len(report.problems)


def seed_demo() -> None:
    """Chạy toàn bộ pipeline với nguồn demo (dữ liệu giả lập)."""
    init_db()
    with job_run("seed_demo") as ctx:
        today = today_vn().isoformat()
        crawl_many(ctx, [("demo", ds, {"today": today})
                         for ds in ("symbols", "prices", "financials", "ratios", "events", "news")])
        ctx.notes.append(f"gắn mã: {tag_news()} liên kết")


# Lịch chạy (giờ VN). checks.py dùng để biết job nào đáng lẽ đã chạy.
JOBS: dict[str, dict] = {
    "symbols_events": {"func": job_symbols_events, "cron": {"hour": 7, "minute": 0}},
    "news": {"func": job_news, "cron": {"hour": "7,12,17", "minute": 0}},
    "prices_eod": {"func": job_prices_eod, "cron": {"day_of_week": "mon-fri", "hour": 15, "minute": 30},
                   "trading_days_only": True},
    "financials": {"func": job_financials, "cron": {"day_of_week": "mon-fri", "hour": 18, "minute": 0}},
    "processing": {"func": job_processing, "cron": {"hour": 18, "minute": 30}},
    "checks": {"func": job_checks, "cron": {"hour": 19, "minute": 0}},
}
