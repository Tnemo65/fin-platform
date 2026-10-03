from __future__ import annotations

import hashlib
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Iterable, Iterator, Sequence, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def utcnow() -> datetime:
    """UTC, không kèm tzinfo (lưu DB thống nhất)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def strip_accents(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def slugify(text: str, max_len: int = 120) -> str:
    text = strip_accents(text).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text[:max_len] or "unknown"


def chunks(items: list[T], size: int) -> Iterator[list[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value != value:  # NaN
            return None
        return float(value)
    s = str(value).strip().replace(",", "")
    if s in {"", "-", "nan", "NaN", "None", "null"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def first_present(d: dict, keys: Iterable[str]):
    """Lấy giá trị theo danh sách tên cột có thể có (không phân biệt hoa thường)."""
    lower = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        if k.lower() in lower and lower[k.lower()] is not None:
            return lower[k.lower()]
    return None


class RateLimiter:
    """Giãn cách tối thiểu giữa 2 request tới cùng một nguồn, dùng chung cho mọi luồng.

    Nhiều luồng crawl song song nhưng tổng tốc độ gửi request tới nguồn vẫn bị chặn ở
    1 request / min_interval giây, nên không bị nguồn chặn vì gửi dồn dập.
    """

    def __init__(self, min_interval: float = 0.0):
        self.min_interval = float(min_interval)
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_at)
            self._next_at = slot + self.min_interval
        if slot > now:
            time.sleep(slot - now)


def thread_map(fn: Callable[[T], R], items: Sequence[T], workers: int) -> list[R]:
    """map() trên nhiều luồng (I/O-bound), giữ nguyên thứ tự kết quả. workers<=1 thì chạy tuần tự."""
    items = list(items)
    if not items:
        return []
    workers = max(1, min(int(workers), len(items)))
    if workers == 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="crawl") as pool:
        return list(pool.map(fn, items))
