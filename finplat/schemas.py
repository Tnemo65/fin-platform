"""Format chung mà mọi nguồn phải trả về sau bước parse.

Nguồn chỉ cần biết cách lấy dữ liệu (fetch) và chuyển raw của mình sang các record dưới đây
(parse). Mọi bước sau đó (chuẩn hoá đơn vị/kỳ, ưu tiên nguồn, khử trùng, gắn mã, upsert)
dùng chung, nên thêm nguồn mới không phải sửa phần còn lại.

Đơn vị tiền tệ để nguyên như nguồn trả về, kèm trường `unit` ("VND", "kVND", "mVND", "bVND");
bước xử lý sẽ quy về VND.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class SymbolRec:
    ticker: str
    exchange: str | None = None
    company_name: str | None = None
    short_name: str | None = None
    industry: str | None = None
    type: str | None = None


@dataclass
class PriceRec:
    ticker: str
    date: date | str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    unit: str = "VND"


@dataclass
class FinancialRec:
    ticker: str
    period: str  # dạng bất kỳ: "Q2/2026", "2026-Q2", (năm, quý) qua year/quarter...
    statement: str  # IS | BS | CF
    item_name: str
    value: float | None
    unit: str = "VND"
    year: int | None = None
    quarter: int | None = None


@dataclass
class RatioRec:
    ticker: str
    period: str
    pe: float | None = None
    pb: float | None = None
    roe: float | None = None  # % hoặc tỷ lệ, bước xử lý tự quy về tỷ lệ
    eps: float | None = None
    eps_unit: str = "VND"
    year: int | None = None
    quarter: int | None = None


@dataclass
class EventRec:
    ticker: str
    event_type: str  # dividend_cash | dividend_stock | issuance | other
    event_date: date | str | None
    value: float | None = None
    unit: str | None = None  # "VND" (tiền/cp), "%" , "shares"
    description: str | None = None


@dataclass
class NewsRec:
    url: str
    title: str
    published_at: datetime | str | None = None
    summary: str | None = None
    content: str | None = None
    kind: str = "news"  # news | disclosure
    ticker_hints: list[str] = field(default_factory=list)  # mã nguồn đã gắn sẵn (nếu có)


DATASET_TYPES = {
    "symbols": SymbolRec,
    "prices": PriceRec,
    "financials": FinancialRec,
    "ratios": RatioRec,
    "events": EventRec,
    "news": NewsRec,
    "disclosures": NewsRec,
}
