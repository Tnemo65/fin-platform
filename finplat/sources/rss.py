"""Tin tức qua RSS (feedparser) + nội dung bài (trafilatura).

Mỗi mục [feeds.<tên>] trong settings.toml thành một nguồn riêng (cafef, vietstock, ...).
Raw lưu nguyên entry RSS và HTML bài viết; trích nội dung bằng trafilatura ở bước parse,
nên đổi cách trích nội dung chỉ cần chạy lại từ raw.
"""
from __future__ import annotations

import calendar
import logging
from datetime import datetime, timezone
from typing import Any, Callable

import feedparser
import requests

from ..config import get_settings
from ..retrying import with_retry
from ..schemas import NewsRec
from ..utils import RateLimiter, thread_map
from .base import Source, register_factory

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; FinancePlatformBot/0.1; +https://example.invalid/bot)"


def http_get(url: str, timeout: int = 20) -> requests.Response:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp


def _entry_time(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc).replace(tzinfo=None).isoformat()
    return entry.get("published") or entry.get("updated")


def extract_text(html: str | None, url: str | None = None) -> str | None:
    if not html:
        return None
    import trafilatura

    return trafilatura.extract(html, url=url, include_comments=False, include_tables=False, favor_precision=True)


class RssSource(Source):
    datasets = ("news",)

    def __init__(self, name: str, label: str, urls: list[str]):
        self.name = name
        self.label = label
        self.urls = urls
        self._limiter = RateLimiter()  # dùng chung cho mọi luồng của nguồn này

    def _get(self, url: str) -> requests.Response:
        """GET có giãn cách + retry; giãn cách nằm trong hàm được retry nên mỗi lần thử vẫn giữ khoảng cách."""

        def attempt():
            self._limiter.wait()
            return http_get(url)

        return with_retry(attempt)

    def fetch(self, dataset: str, skip: Callable[[str], bool] | None = None, **params: Any) -> list[dict]:
        s = get_settings()
        limit = int(s.general("max_articles_per_feed", 40))
        workers = int(s.general("crawl_workers", 4))
        self._limiter.min_interval = float(s.general("request_delay", 0.4))

        # 1. Đọc các feed (song song)
        def read_feed(feed_url: str) -> tuple[Any, str | None]:
            try:
                return feedparser.parse(self._get(feed_url).content), None
            except Exception as e:  # noqa: BLE001
                log.warning("%s: không đọc được feed %s: %s", self.name, feed_url, e)
                return None, f"{type(e).__name__}: {e}"

        out: list[dict] = []
        errors = []
        for feed_url, (feed, err) in zip(self.urls, thread_map(read_feed, self.urls, workers)):
            if err:
                errors.append({"_error": err, "feed_url": feed_url})
                continue
            for entry in feed.entries[:limit]:
                link = entry.get("link")
                if not link:
                    continue
                rec = {
                    "feed_url": feed_url,
                    "title": entry.get("title"),
                    "link": link,
                    "summary": entry.get("summary"),
                    "published": _entry_time(entry),
                    "tags": [t.get("term") for t in entry.get("tags", []) if t.get("term")],
                    "html": None,
                }
                if skip and skip(link):
                    rec["_skipped_content"] = True  # đã có trong DB, không tải lại bài
                out.append(rec)

        # 2. Tải nội dung bài mới (song song, giãn cách request_delay giữa các request, lỗi tạm thời tự thử lại)
        def load_article(rec: dict) -> None:
            try:
                rec["html"] = self._get(rec["link"]).text
            except Exception as e:  # noqa: BLE001
                rec["_content_error"] = f"{type(e).__name__}: {e}"

        thread_map(load_article, [r for r in out if not r.get("_skipped_content")], workers)

        if not out and errors:
            # Mọi feed đều lỗi -> báo lỗi để job ghi failed
            raise RuntimeError("; ".join(e["_error"] for e in errors))
        return out + errors

    def parse(self, dataset: str, raw: list[dict], params: dict | None = None) -> list[NewsRec]:
        out = []
        for r in raw:
            if "_error" in r or not r.get("link") or not r.get("title"):
                continue
            out.append(
                NewsRec(
                    url=r["link"],
                    title=r["title"].strip(),
                    published_at=r.get("published"),
                    summary=_clean_summary(r.get("summary")),
                    content=extract_text(r.get("html"), r["link"]),
                    kind="news",
                )
            )
        return out


def _clean_summary(html: str | None) -> str | None:
    if not html:
        return None
    from lxml import html as lh

    try:
        return lh.fromstring(html).text_content().strip() or None
    except Exception:  # noqa: BLE001
        return html


@register_factory
def _from_config() -> list[Source]:
    return [RssSource(name, cfg.get("label", name), cfg["urls"]) for name, cfg in get_settings().feeds.items()]
