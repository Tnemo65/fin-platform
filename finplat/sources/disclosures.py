"""Công bố thông tin trên HOSE và HNX.

Hai sàn không có RSS ổn định, nên nguồn này tải trang danh sách tin công bố (HTML), lưu raw,
rồi parse bằng XPath cấu hình trong [disclosures.<tên>]. Nếu sàn đổi giao diện chỉ cần sửa
XPath/URL trong settings.toml rồi chạy lại xử lý từ raw.

Tin công bố thường có mã ở đầu tiêu đề ("VNM: Nghị quyết HĐQT ..."), được dùng làm gợi ý mã.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from lxml import html as lh

from ..config import get_settings
from ..schemas import NewsRec
from .base import Source, register_factory
from .rss import http_get

TITLE_TICKER = re.compile(r"^\s*([A-Z][A-Z0-9]{2})\s*[:\-–]")
DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")


class DisclosureSource(Source):
    datasets = ("disclosures",)

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.label = cfg.get("label", name)
        self.url = cfg["url"]
        self.item_xpath = cfg["item_xpath"]
        self.base_url = cfg.get("base_url", self.url)

    def fetch(self, dataset: str, **params: Any) -> list[dict]:
        resp = http_get(self.url)
        return [{"page_url": self.url, "fetched_html": resp.text}]

    def parse(self, dataset: str, raw: list[dict], params: dict | None = None) -> list[NewsRec]:
        out: list[NewsRec] = []
        for page in raw:
            html = page.get("fetched_html")
            if not html:
                continue
            tree = lh.fromstring(html)
            for a in tree.xpath(self.item_xpath):
                title = " ".join(a.text_content().split())
                href = a.get("href")
                if not title or not href or len(title) < 8:
                    continue
                # Ngày thường nằm cùng dòng (tr/li) với link
                row = a.getparent()
                while row is not None and row.tag not in ("tr", "li", "div"):
                    row = row.getparent()
                published = _find_date(row.text_content() if row is not None else "")
                m = TITLE_TICKER.match(title)
                out.append(
                    NewsRec(
                        url=urljoin(self.base_url, href),
                        title=title,
                        published_at=published,
                        kind="disclosure",
                        ticker_hints=[m.group(1)] if m else [],
                    )
                )
        return out


def _find_date(text: str) -> str | None:
    m = DATE_RE.search(text or "")
    if not m:
        return None
    d, mo, y, hh, mm = m.groups()
    try:
        # Giờ trên trang sàn là giờ VN
        return datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), tzinfo=get_settings().tz).isoformat()
    except ValueError:
        return None


@register_factory
def _from_config() -> list[Source]:
    return [DisclosureSource(name, cfg) for name, cfg in get_settings().disclosures.items()]
