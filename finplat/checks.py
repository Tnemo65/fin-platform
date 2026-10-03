"""Kiểm tra dữ liệu (job 19:00) + gửi báo cáo job lỗi. data_status() dùng chung cho trang tình trạng."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone

import requests
from sqlalchemy import func, select

from .config import get_settings
from .db import session_scope
from .models import JobRun, News, PriceDaily, RawBatch
from .trading_calendar import is_trading_day, last_trading_day, today_vn
from .utils import utcnow

log = logging.getLogger(__name__)

DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


@dataclass
class Report:
    day: str
    problems: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return f"{len(self.problems)} vấn đề" if self.problems else "Dữ liệu ổn"

    def to_markdown(self) -> str:
        lines = [f"# Báo cáo dữ liệu {self.day}", "", f"**{self.summary()}**", ""]
        if self.problems:
            lines += ["## Vấn đề", *[f"- {p}" for p in self.problems], ""]
        lines += ["## Thông tin", *[f"- {i}" for i in self.info]]
        return "\n".join(lines)


def _days(spec) -> set[int]:
    if spec is None:
        return set(range(7))
    out: set[int] = set()
    for part in str(spec).split(","):
        if "-" in part:
            a, b = part.split("-")
            out |= set(range(DOW[a], DOW[b] + 1))
        else:
            out.add(DOW[part])
    return out


def expected_runs_today(now_vn: datetime) -> list[tuple[str, datetime]]:
    """(job, giờ chạy dự kiến theo UTC) của các lần chạy hôm nay đã tới giờ."""
    from .jobs import JOBS

    tz = get_settings().tz
    out = []
    for name, spec in JOBS.items():
        cron = spec["cron"]
        if now_vn.weekday() not in _days(cron.get("day_of_week")):
            continue
        if spec.get("trading_days_only") and not is_trading_day(now_vn.date()):
            continue
        for h in str(cron["hour"]).split(","):
            at = datetime.combine(now_vn.date(), time(int(h), int(cron.get("minute", 0))), tzinfo=tz)
            if at + timedelta(minutes=10) <= now_vn:
                out.append((name, at.astimezone(timezone.utc).replace(tzinfo=None)))
    return out


def run_checks(now_vn: datetime | None = None, notify: bool = True) -> Report:
    s = get_settings()
    now_vn = now_vn or datetime.now(s.tz)
    if now_vn.tzinfo is None:
        now_vn = now_vn.replace(tzinfo=s.tz)
    report = Report(day=now_vn.date().isoformat())
    since = utcnow() - timedelta(hours=24)

    with session_scope() as sess:
        # 1. Job lỗi trong 24h
        bad = sess.scalars(
            select(JobRun).where(JobRun.started_at >= since, JobRun.status.in_(["failed", "partial"]))
            .order_by(JobRun.started_at)
        ).all()
        for r in bad:
            first = (r.error or r.message or "").strip().splitlines()[-1:] or [""]
            report.problems.append(f"Job `{r.job_name}` {r.status} lúc {r.started_at:%H:%M} UTC: {first[0][:200]}")

        # 2. Job đáng lẽ đã chạy hôm nay nhưng không có bản ghi
        for name, at in expected_runs_today(now_vn):
            ran = sess.scalar(select(func.count()).select_from(JobRun).where(
                JobRun.job_name == name, JobRun.started_at >= at - timedelta(minutes=5),
                JobRun.started_at <= at + timedelta(hours=3)))
            if not ran and name != "checks":
                report.problems.append(f"Job `{name}` không chạy lúc {at:%H:%M} UTC")

        # 3. Độ phủ giá EOD của ngày giao dịch gần nhất
        from .jobs import active_tickers

        d = today_vn()
        ref = last_trading_day(d if now_vn.time() >= time(16, 0) else d - timedelta(days=1))
        n_active = len(active_tickers())
        n_price = sess.scalar(select(func.count()).select_from(PriceDaily).where(PriceDaily.date == ref)) or 0
        report.info.append(f"Giá EOD {ref}: {n_price}/{n_active} mã")
        if n_active and n_price < 0.9 * n_active:
            report.problems.append(f"Giá EOD {ref} chỉ có {n_price}/{n_active} mã (<90%)")

        # 4. Độ mới của từng nguồn
        for row in source_freshness(sess):
            report.info.append(f"{row['source']}/{row['dataset']}: lần cuối {row['last_fetched']:%Y-%m-%d %H:%M} UTC")
            limit = 12 if row["dataset"] in ("news", "disclosures") else 72
            if row["last_fetched"] < utcnow() - timedelta(hours=limit):
                report.problems.append(f"Nguồn {row['source']}/{row['dataset']} không có dữ liệu mới quá {limit}h")

        # 5. Batch raw xử lý lỗi
        failed = sess.scalar(select(func.count()).select_from(RawBatch).where(RawBatch.status == "failed")) or 0
        if failed:
            report.problems.append(f"{failed} batch raw xử lý lỗi (chạy `python -m finplat process`)")
        untagged = sess.scalar(select(func.count()).select_from(News).where(News.tagged_at.is_(None))) or 0
        report.info.append(f"Tin chưa gắn mã: {untagged}")

    _save_and_notify(report, notify)
    return report


def source_freshness(sess) -> list[dict]:
    rows = sess.execute(
        select(RawBatch.source, RawBatch.dataset, func.max(RawBatch.fetched_at), func.count())
        .where(RawBatch.record_count > 0).group_by(RawBatch.source, RawBatch.dataset)
        .order_by(RawBatch.source, RawBatch.dataset)
    ).all()
    return [{"source": a, "dataset": b, "last_fetched": c, "batches": n} for a, b, c, n in rows]


def data_status(days: int = 7) -> dict:
    """Cho trang tình trạng dữ liệu: lần cập nhật gần nhất từng nguồn + job lỗi + lần chạy gần nhất từng job."""
    with session_scope() as sess:
        fresh = source_freshness(sess)
        last_runs = []
        for (name,) in sess.execute(select(JobRun.job_name).distinct()).all():
            r = sess.scalars(select(JobRun).where(JobRun.job_name == name).order_by(JobRun.started_at.desc()).limit(1)).first()
            last_runs.append(_run_dict(r))
        failed = sess.scalars(
            select(JobRun).where(JobRun.started_at >= utcnow() - timedelta(days=days),
                                 JobRun.status.in_(["failed", "partial"])).order_by(JobRun.started_at.desc())
        ).all()
        batches = dict(sess.execute(select(RawBatch.status, func.count()).group_by(RawBatch.status)).all())
    return {
        "sources": [{**f, "last_fetched": f["last_fetched"].isoformat() + "Z"} for f in fresh],
        "last_runs": sorted(last_runs, key=lambda r: r["job_name"]),
        "failed_jobs": [_run_dict(r) for r in failed],
        "raw_batches": batches,
    }


def _run_dict(r: JobRun) -> dict:
    return {
        "id": r.id, "job_name": r.job_name, "status": r.status, "records": r.records,
        "started_at": r.started_at.isoformat() + "Z" if r.started_at else None,
        "finished_at": r.finished_at.isoformat() + "Z" if r.finished_at else None,
        "message": r.message, "error": r.error,
    }


def _save_and_notify(report: Report, notify: bool) -> None:
    s = get_settings()
    s.report_dir.mkdir(parents=True, exist_ok=True)
    (s.report_dir / f"{report.day}.md").write_text(report.to_markdown(), encoding="utf-8")
    if not notify or not report.problems:
        return
    text = report.to_markdown()
    try:
        if s.notify_webhook_url:  # Slack/Discord/Google Chat incoming webhook
            requests.post(s.notify_webhook_url, json={"text": text, "content": text[:1900]}, timeout=15)
        if s.telegram_bot_token and s.telegram_chat_id:
            requests.post(f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
                          json={"chat_id": s.telegram_chat_id, "text": text[:4000]}, timeout=15)
    except Exception as e:  # noqa: BLE001
        log.error("Gửi báo cáo lỗi: %s", e)
