"""Lưu raw trước khi xử lý.

Mỗi lần fetch ghi một file data/raw/<source>/<dataset>/<YYYY-MM-DD>/<batch>.jsonl.gz và
một dòng raw_batches (status=pending). Bước xử lý đọc lại từ file, nên khi xử lý lỗi chỉ cần
chạy lại từ raw, không phải crawl lại.
"""
from __future__ import annotations

import gzip
import json
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from .config import get_settings
from .models import RawBatch
from .utils import utcnow


class RawStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or get_settings().raw_dir)

    def save(
        self,
        session: Session,
        source: str,
        dataset: str,
        records: list[dict],
        params: dict | None = None,
        job_run_id: int | None = None,
    ) -> RawBatch:
        now = utcnow()
        batch_id = f"{now:%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"
        path = self.root / source / dataset / f"{now:%Y-%m-%d}" / f"{batch_id}.jsonl.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        batch = RawBatch(
            id=batch_id,
            source=source,
            dataset=dataset,
            params=json.loads(json.dumps(params or {}, default=str)),
            path=str(path),
            record_count=len(records),
            fetched_at=now,
            status="pending",
            job_run_id=job_run_id,
        )
        session.add(batch)
        session.flush()
        return batch

    @staticmethod
    def load(batch: RawBatch) -> list[dict]:
        with gzip.open(batch.path, "rt", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
