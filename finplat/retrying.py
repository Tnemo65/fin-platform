"""Retry cho các hàm crawl (tenacity): lỗi tạm thời thì thử lại với backoff mũ + jitter.

Dùng: `with_retry(fn, *args, **kwargs)` hoặc decorator `@retryable`. Chỉ thử lại lỗi *tạm thời*
(mất kết nối, timeout, HTTP 408/425/429/5xx, lỗi socket, thông báo kiểu "rate limit"); lỗi dữ liệu
(404, ValueError, KeyError...) ném ra ngay vì thử lại cũng không khác. Bị 429/503 có `Retry-After`
thì chờ đúng số giây đó (giới hạn bởi wait_max). Cấu hình trong [retry] của settings.toml.

Bộ giãn cách request (RateLimiter) đặt *bên trong* hàm được retry, nên mỗi lần thử lại vẫn
giữ khoảng cách với các luồng khác.
"""
from __future__ import annotations

import functools
import logging
import random
from email.utils import parsedate_to_datetime
from typing import Callable, TypeVar

import requests
from tenacity import RetryCallState, Retrying, before_sleep_log, retry_if_exception, stop_after_attempt

from .config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T")

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
# Lỗi lập trình / dữ liệu: thử lại vô ích
NON_RETRYABLE = (ValueError, KeyError, TypeError, AttributeError, NotImplementedError, AssertionError)
TRANSIENT_HINTS = ("timeout", "timed out", "rate limit", "too many requests", "temporarily", "connection reset",
                   "connection aborted", "remote end closed", "service unavailable", "bad gateway", "gateway time")


def is_transient(exc: BaseException) -> bool:
    """Lỗi có đáng thử lại không."""
    if isinstance(exc, requests.HTTPError):
        resp = getattr(exc, "response", None)
        return resp is not None and resp.status_code in RETRYABLE_STATUS
    if isinstance(exc, (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)):
        return True
    if isinstance(exc, NON_RETRYABLE):
        return False
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    # Thư viện bên thứ ba (vnstock...) ném exception kiểu riêng: dò theo thông báo
    msg = str(exc).lower()
    return any(h in msg for h in TRANSIENT_HINTS) or any(f" {c}" in f" {msg}" for c in ("429", "502", "503", "504"))


def retry_after_seconds(exc: BaseException) -> float | None:
    """Giá trị Retry-After (giây) nếu server trả về, hỗ trợ cả dạng số giây và HTTP-date."""
    resp = getattr(exc, "response", None)
    value = resp.headers.get("Retry-After") if resp is not None and getattr(resp, "headers", None) else None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) - parsedate_to_datetime(resp.headers.get("Date") or "")).total_seconds())
        except (TypeError, ValueError):
            return None


def _retry_config() -> dict:
    cfg = get_settings().raw.get("retry", {})
    return {
        "attempts": max(1, int(cfg.get("attempts", 3))),
        "wait_min": float(cfg.get("wait_min", 1.0)),
        "wait_max": float(cfg.get("wait_max", 20.0)),
    }


def make_wait(wait_min: float, wait_max: float) -> Callable[[RetryCallState], float]:
    """Backoff mũ + jitter; nếu lỗi có Retry-After thì chờ theo đó (không vượt wait_max)."""

    def wait(state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome and state.outcome.failed else None
        hinted = retry_after_seconds(exc) if exc is not None else None
        if hinted is not None:
            return min(hinted, wait_max)
        base = wait_min * (2 ** (state.attempt_number - 1))
        return min(wait_max, base + random.uniform(0, base))

    return wait


def with_retry(fn: Callable[..., T], *args, attempts: int | None = None, **kwargs) -> T:
    """Gọi fn(*args, **kwargs); lỗi tạm thời thì thử lại theo cấu hình [retry]. Hết lượt thì ném lỗi gốc."""
    cfg = _retry_config()
    retrying = Retrying(
        stop=stop_after_attempt(attempts or cfg["attempts"]),
        wait=make_wait(cfg["wait_min"], cfg["wait_max"]),
        retry=retry_if_exception(is_transient),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    return retrying(fn, *args, **kwargs)


def retryable(fn: Callable[..., T]) -> Callable[..., T]:
    """Decorator tương đương with_retry."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return with_retry(fn, *args, **kwargs)

    return wrapper
