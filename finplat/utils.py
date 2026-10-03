from __future__ import annotations

import hashlib
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Callable, Iterable, Iterator, TypeVar

T = TypeVar("T")


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


def retry(fn: Callable[[], T], attempts: int = 3, delay: float = 2.0, exceptions=(Exception,)) -> T:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except exceptions as e:  # noqa: PERF203
            last = e
            if i < attempts - 1:
                time.sleep(delay * (2**i))
    assert last is not None
    raise last


def first_present(d: dict, keys: Iterable[str]):
    """Lấy giá trị theo danh sách tên cột có thể có (không phân biệt hoa thường)."""
    lower = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        if k.lower() in lower and lower[k.lower()] is not None:
            return lower[k.lower()]
    return None
