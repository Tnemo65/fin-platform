"""Phần dùng chung cho nguồn gọi HTTP trực tiếp theo từng mã (VNDirect, CafeF...).

Mỗi nguồn vẫn là một module riêng, cùng interface Source. Lớp này chỉ gói: session requests có User-Agent,
giãn cách request dùng chung cho mọi luồng, retry lỗi tạm thời, và chạy song song theo mã. Lỗi một mã
được ghi thành bản ghi {"ticker", "_error"} để job báo partial, không làm hỏng cả batch.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import requests

from ..config import get_settings
from ..retrying import with_retry
from ..utils import RateLimiter, thread_map
from .base import Source

log = logging.getLogger(__name__)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")


class HttpTickerSource(Source):
    timeout = 20

    def __init__(self):
        self._limiter = RateLimiter()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"})

    def request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        """Một request JSON có giãn cách + retry. Trả về JSON đã parse; HTTP lỗi hoặc không phải JSON thì ném lỗi."""

        def attempt():
            self._limiter.wait()
            resp = self._session.request(method, url, timeout=self.timeout, **kwargs)
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError as e:
                raise RuntimeError(f"{self.name}: phản hồi không phải JSON ({resp.status_code}, {resp.text[:120]!r})") from e

        return with_retry(attempt)

    def per_ticker(self, tickers: list[str], fn: Callable[[str], list[dict]]) -> list[dict]:
        s = get_settings()
        self._limiter.min_interval = s.request_delay(self.name)

        def one(t: str) -> list[dict]:
            try:
                recs = fn(t)
                for r in recs:
                    r.setdefault("ticker", t)
                return recs
            except Exception as e:  # noqa: BLE001
                log.warning("%s: lỗi %s: %s", self.name, t, e)
                return [{"ticker": t, "_error": f"{type(e).__name__}: {e}"}]

        out: list[dict] = []
        for recs in thread_map(one, tickers, int(s.general("crawl_workers", 4))):
            out.extend(recs)
        return out
