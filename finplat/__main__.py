"""CLI: python -m finplat <lệnh>

  init-db                     Tạo bảng
  run <job> [--tickers A,B]   Chạy một job ngay (symbols_events, news, prices_eod, financials, processing, checks)
  process [--retry-failed]    Xử lý các batch raw còn tồn
  reprocess [--since D] [--source S] [--dataset D]   Chạy lại xử lý từ raw, không crawl lại
  tag [--all]                 Gắn mã cho tin (--all: gắn lại toàn bộ)
  scheduler                   Chạy lịch hằng ngày
  api [--port 8000]           Chạy FastAPI
  seed-demo                   Nạp dữ liệu GIẢ LẬP để thử UI
  status                      In tình trạng dữ liệu
  sources                     Liệt kê nguồn đã đăng ký
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="finplat", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    r = sub.add_parser("run")
    r.add_argument("job")
    r.add_argument("--tickers", help="Danh sách mã, cách nhau bởi dấu phẩy")
    r.add_argument("--force", action="store_true", help="prices_eod: chạy cả ngày nghỉ")
    r.add_argument("--full", action="store_true", help="financials: chạy toàn bộ mã")
    pr = sub.add_parser("process")
    pr.add_argument("--retry-failed", action="store_true")
    rp = sub.add_parser("reprocess")
    rp.add_argument("--since")
    rp.add_argument("--source")
    rp.add_argument("--dataset")
    t = sub.add_parser("tag")
    t.add_argument("--all", action="store_true")
    sub.add_parser("scheduler")
    a = sub.add_parser("api")
    a.add_argument("--host", default="0.0.0.0")
    a.add_argument("--port", type=int, default=8000)
    sub.add_parser("seed-demo")
    sub.add_parser("status")
    sub.add_parser("sources")
    args = p.parse_args(argv)

    from .db import init_db

    init_db()
    if args.cmd == "init-db":
        print("OK")
    elif args.cmd == "run":
        from . import jobs

        if args.job not in jobs.JOBS:
            print(f"Không có job {args.job}. Các job: {', '.join(jobs.JOBS)}", file=sys.stderr)
            return 2
        kwargs = {}
        if args.tickers:
            kwargs["tickers"] = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]
        if args.force and args.job == "prices_eod":
            kwargs["force"] = True
        if args.full and args.job == "financials":
            kwargs["full"] = True
        try:
            jobs.JOBS[args.job]["func"](**kwargs)
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 1
        # Job ghi failed vào job_runs (nguồn lỗi, không có exception) -> exit 1 để cron/CI biết
        status = jobs.last_run_status(args.job)
        if status == "failed":
            print(f"Job {args.job} failed, xem bảng job_runs hoặc `python -m finplat status`", file=sys.stderr)
            return 1
    elif args.cmd == "process":
        from .processing.pipeline import process_pending, tag_news

        res = process_pending(include_failed=args.retry_failed)
        res["tagged"] = tag_news()
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.cmd == "reprocess":
        from .processing.pipeline import reprocess, tag_news

        since = datetime.fromisoformat(args.since) if args.since else None
        res = reprocess(since, args.source, args.dataset)
        res["tagged"] = tag_news()
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.cmd == "tag":
        from .processing.pipeline import tag_news

        print(f"Đã gắn {tag_news(all_news=args.all)} liên kết tin-mã")
    elif args.cmd == "scheduler":
        from .scheduler import main as sched_main

        sched_main()
    elif args.cmd == "api":
        import uvicorn

        uvicorn.run("finplat.api.main:app", host=args.host, port=args.port)
    elif args.cmd == "seed-demo":
        from .jobs import seed_demo

        seed_demo()
        print("Đã nạp dữ liệu demo (giả lập) cho VNM, FPT, HPG, VCB, SHS")
    elif args.cmd == "status":
        from .checks import data_status

        print(json.dumps(data_status(), ensure_ascii=False, indent=2, default=str))
    elif args.cmd == "sources":
        from .sources import all_sources

        for name, src in sorted(all_sources().items()):
            print(f"{name:15s} {', '.join(src.datasets)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
