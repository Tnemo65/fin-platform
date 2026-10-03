"""Schema các bảng core.

Quy ước:
- Khoá chính của dữ liệu là mã + ngày (hoặc kỳ báo cáo dạng 2026Q2).
- Mọi giá trị tiền tệ đã quy về VND.
- `source` là nguồn đang giữ dòng dữ liệu, `source_priority` là thứ hạng của nguồn đó
  (nhỏ hơn = ưu tiên hơn). Upsert chỉ ghi đè khi nguồn mới ưu tiên bằng hoặc hơn.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


from .utils import utcnow as _now


class SourceMixin:
    source: Mapped[str] = mapped_column(String(32))
    source_priority: Mapped[int] = mapped_column(Integer, default=99)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Symbol(SourceMixin, Base):
    __tablename__ = "symbols"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    exchange: Mapped[str | None] = mapped_column(String(16))
    company_name: Mapped[str | None] = mapped_column(String(255))
    short_name: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(128))
    type: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PriceDaily(SourceMixin, Base):
    __tablename__ = "price_daily"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)


class FinancialItem(SourceMixin, Base):
    """BCTC dạng dài: mỗi dòng là một chỉ tiêu của một kỳ."""

    __tablename__ = "financial_items"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    period: Mapped[str] = mapped_column(String(8), primary_key=True)  # 2026Q2
    statement: Mapped[str] = mapped_column(String(4), primary_key=True)  # IS | BS | CF
    item_code: Mapped[str] = mapped_column(String(128), primary_key=True)
    item_name: Mapped[str | None] = mapped_column(String(255))
    year: Mapped[int] = mapped_column(Integer)
    quarter: Mapped[int] = mapped_column(Integer)  # 1..4, 0 = cả năm
    value: Mapped[float | None] = mapped_column(Float)  # VND

    __table_args__ = (Index("ix_fin_ticker_stmt", "ticker", "statement"),)


class Ratio(SourceMixin, Base):
    __tablename__ = "ratios"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    period: Mapped[str] = mapped_column(String(8), primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    quarter: Mapped[int] = mapped_column(Integer)
    pe: Mapped[float | None] = mapped_column(Float)
    pb: Mapped[float | None] = mapped_column(Float)
    roe: Mapped[float | None] = mapped_column(Float)  # tỷ lệ, 0.18 = 18%
    eps: Mapped[float | None] = mapped_column(Float)  # VND/cp


# ---- Bảng theo từng nguồn: giữ nguyên giá trị MỌI nguồn đã crawl (không chỉ nguồn thắng ưu tiên),
# để so sánh chéo, phát hiện lệch và truy vết. Bảng core ở trên là bản hợp nhất theo [priority].
class PriceDailySource(Base):
    __tablename__ = "price_daily_by_source"
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (Index("ix_pds_ticker_date", "ticker", "date"),)


class FinancialItemSource(Base):
    __tablename__ = "financial_items_by_source"
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    period: Mapped[str] = mapped_column(String(8), primary_key=True)
    statement: Mapped[str] = mapped_column(String(4), primary_key=True)
    item_code: Mapped[str] = mapped_column(String(128), primary_key=True)
    item_name: Mapped[str | None] = mapped_column(String(255))
    value: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (Index("ix_fis_ticker_period", "ticker", "period"),)


class RatioSource(Base):
    __tablename__ = "ratios_by_source"
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    period: Mapped[str] = mapped_column(String(8), primary_key=True)
    pe: Mapped[float | None] = mapped_column(Float)
    pb: Mapped[float | None] = mapped_column(Float)
    roe: Mapped[float | None] = mapped_column(Float)
    eps: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class CorporateEvent(SourceMixin, Base):
    """Cổ tức tiền/cổ phiếu, phát hành thêm..."""

    __tablename__ = "corporate_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # hash(ticker, type, date, mô tả)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    event_type: Mapped[str] = mapped_column(String(32))  # dividend_cash | dividend_stock | issuance | other
    event_date: Mapped[date | None] = mapped_column(Date)
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(16))  # VND | % | shares
    description: Mapped[str | None] = mapped_column(Text)


class News(SourceMixin, Base):
    __tablename__ = "news"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # sha1(URL chuẩn hoá)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    title_hash: Mapped[str] = mapped_column(String(40), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="news")  # news | disclosure
    tagged_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class NewsTicker(Base):
    __tablename__ = "news_tickers"
    news_id: Mapped[str] = mapped_column(String(40), ForeignKey("news.id", ondelete="CASCADE"), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True, index=True)
    match_type: Mapped[str] = mapped_column(String(16))  # explicit | name | ticker
    score: Mapped[float] = mapped_column(Float)


class RawBatch(Base):
    """Mỗi lần fetch một nguồn tạo một batch raw (file .jsonl.gz) và một dòng ở đây."""

    __tablename__ = "raw_batches"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    dataset: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict | None] = mapped_column(JSON)
    path: Mapped[str] = mapped_column(Text)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|processed|failed
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(Text)
    job_run_id: Mapped[int | None] = mapped_column(Integer)
    # Độ phủ của lần crawl: số mã/feed yêu cầu, số mục có dữ liệu, số mục lỗi (để biết nguồn nào crawl thiếu)
    requested: Mapped[int | None] = mapped_column(Integer)
    ok_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|success|partial|failed|skipped
    records: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
