"""Lịch chạy hằng ngày bằng APScheduler (giờ VN). Chạy: python -m finplat scheduler"""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_settings
from .db import init_db
from .jobs import JOBS

log = logging.getLogger(__name__)


def _safe(name, func):
    def run():
        try:
            func()
        except Exception:  # job_runs đã ghi lỗi; không để scheduler dừng
            log.error("Job %s kết thúc với lỗi (xem bảng job_runs)", name)

    return run


def build_scheduler() -> BlockingScheduler:
    tz = get_settings().tz
    sched = BlockingScheduler(timezone=tz)
    for name, spec in JOBS.items():
        sched.add_job(_safe(name, spec["func"]), CronTrigger(timezone=tz, **spec["cron"]), id=name, name=name,
                      max_instances=1, coalesce=True, misfire_grace_time=3600)
    return sched


def main() -> None:
    init_db()
    sched = build_scheduler()
    for job in sched.get_jobs():
        log.info("Đã lên lịch %s: %s", job.id, job.trigger)
    sched.start()
