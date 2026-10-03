"""Raw -> bảng core: parse theo nguồn, chuẩn hoá, ưu tiên nguồn, khử trùng, gắn mã, upsert.

Mọi bước đều idempotent: chạy lại một batch (hoặc toàn bộ raw) cho cùng kết quả.
"""
from __future__ import annotations

import logging
import traceback
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope, upsert
from ..models import (CorporateEvent, FinancialItem, FinancialItemSource, News, NewsTicker, PriceDaily,
                      PriceDailySource, RawBatch, Ratio, RatioSource, Symbol)
from ..raw_store import RawStore
from ..schemas import EventRec, FinancialRec, NewsRec, PriceRec, RatioRec, SymbolRec
from ..sources import get_source
from ..utils import sha1, utcnow
from .normalize import (
    canonical_url,
    item_code,
    normalize_period,
    parse_date,
    parse_datetime_utc,
    title_hash,
    to_vnd,
    unit_from_name,
    url_hash,
)
from .tagger import Tagger

log = logging.getLogger(__name__)


def _prio(dataset: str, source: str) -> dict:
    return {"source": source, "source_priority": get_settings().priority_rank(dataset, source)}


# ------------------------------------------------------------------ processors
def _keep_by_source(session: Session, model, rows: list[dict], key_cols: list[str]) -> None:
    """Giữ giá trị của MỌI nguồn trong bảng *_by_source (không lọc theo ưu tiên), để so sánh chéo giữa các nguồn."""
    cols = {c.name for c in model.__table__.columns}
    upsert(session, model, [{k: v for k, v in r.items() if k in cols} for r in rows], key_cols, respect_priority=False)


def process_symbols(session: Session, recs: list[SymbolRec], source: str) -> int:
    p = _prio("symbols", source)
    rows = [
        {"ticker": r.ticker.strip().upper(), "exchange": r.exchange, "company_name": r.company_name,
         "short_name": r.short_name, "industry": r.industry, "type": r.type, "is_active": True, **p}
        for r in recs
        if r.ticker
    ]
    return upsert(session, Symbol, rows, ["ticker"])


def process_prices(session: Session, recs: list[PriceRec], source: str) -> int:
    p = _prio("prices", source)
    rows = []
    for r in recs:
        if r.close is None:
            continue
        o, h, lo, c = (to_vnd(v, r.unit) for v in (r.open, r.high, r.low, r.close))
        if h is not None and lo is not None and h < lo:
            log.warning("Bỏ dòng giá lỗi %s %s: high < low", r.ticker, r.date)
            continue
        rows.append({"ticker": r.ticker.upper(), "date": parse_date(r.date), "open": o, "high": h, "low": lo,
                     "close": c, "volume": int(r.volume) if r.volume is not None else None, **p})
    _keep_by_source(session, PriceDailySource, rows, ["source", "ticker", "date"])
    return upsert(session, PriceDaily, rows, ["ticker", "date"])


def process_financials(session: Session, recs: list[FinancialRec], source: str) -> int:
    p = _prio("financials", source)
    rows = []
    for r in recs:
        period, year, quarter = normalize_period(r.period or None, r.year, r.quarter)
        unit = unit_from_name(r.item_name, r.unit)
        rows.append({"ticker": r.ticker.upper(), "period": period, "year": year, "quarter": quarter,
                     "statement": r.statement, "item_code": item_code(r.item_name), "item_name": r.item_name,
                     "value": to_vnd(r.value, unit), **p})
    _keep_by_source(session, FinancialItemSource, rows, ["source", "ticker", "period", "statement", "item_code"])
    return upsert(session, FinancialItem, rows, ["ticker", "period", "statement", "item_code"])


def process_ratios(session: Session, recs: list[RatioRec], source: str) -> int:
    p = _prio("ratios", source)
    rows = []
    for r in recs:
        period, year, quarter = normalize_period(r.period or None, r.year, r.quarter)
        roe = r.roe
        if roe is not None and abs(roe) > 1.5:  # nguồn trả % (18.5) -> tỷ lệ (0.185)
            roe = roe / 100
        rows.append({"ticker": r.ticker.upper(), "period": period, "year": year, "quarter": quarter,
                     "pe": r.pe, "pb": r.pb, "roe": roe, "eps": to_vnd(r.eps, r.eps_unit), **p})
    _keep_by_source(session, RatioSource, rows, ["source", "ticker", "period"])
    return upsert(session, Ratio, rows, ["ticker", "period"])


def process_events(session: Session, recs: list[EventRec], source: str) -> int:
    p = _prio("events", source)
    rows = []
    for r in recs:
        d = parse_date(r.event_date)
        eid = sha1(f"{r.ticker.upper()}|{r.event_type}|{d}|{(r.description or '').strip().lower()}")
        rows.append({"id": eid, "ticker": r.ticker.upper(), "event_type": r.event_type, "event_date": d,
                     "value": r.value, "unit": r.unit, "description": r.description, **p})
    return upsert(session, CorporateEvent, rows, ["id"])


def process_news(session: Session, recs: list[NewsRec], source: str) -> int:
    """Khử trùng: cùng URL (đã chuẩn hoá) -> cùng id, upsert theo ưu tiên nguồn.
    Khác URL nhưng trùng tiêu đề -> bài đăng lại, bỏ qua."""
    p = _prio("news", source)
    candidates: dict[str, dict] = {}
    hints: dict[str, list[str]] = {}
    seen_titles: dict[str, str] = {}
    for r in recs:
        if not r.url or not r.title:
            continue
        nid, th = url_hash(r.url), title_hash(r.title)
        if th in seen_titles and seen_titles[th] != nid:
            continue
        seen_titles[th] = nid
        candidates[nid] = {"id": nid, "url": canonical_url(r.url), "title": r.title.strip(), "title_hash": th,
                           "summary": r.summary, "content": r.content,
                           "published_at": parse_datetime_utc(r.published_at), "kind": r.kind,
                           "tagged_at": None, **p}
        if r.ticker_hints:
            hints[nid] = r.ticker_hints
    if not candidates:
        return 0

    # Trùng tiêu đề với tin đã có (id khác) -> bài đăng lại
    existing = session.execute(
        select(News.id, News.title_hash).where(News.title_hash.in_([c["title_hash"] for c in candidates.values()]))
    ).all()
    dup_titles = {th: nid for nid, th in existing}
    rows = [c for c in candidates.values() if dup_titles.get(c["title_hash"], c["id"]) == c["id"]]
    skipped = len(candidates) - len(rows)
    if skipped:
        log.info("%s: bỏ %d tin đăng lại (trùng tiêu đề)", source, skipped)
    # Không xoá nội dung đã có nếu lần này không tải được bài
    for row in rows:
        if row["content"] is None:
            row.pop("content")
    n = upsert(session, News, rows, ["id"])
    hint_rows = [{"news_id": nid, "ticker": t.upper(), "match_type": "source", "score": 1.0}
                 for nid, ts in hints.items() if nid in {r["id"] for r in rows} for t in ts]
    upsert(session, NewsTicker, hint_rows, ["news_id", "ticker"], respect_priority=False)
    return n


PROCESSORS = {
    "symbols": process_symbols,
    "prices": process_prices,
    "financials": process_financials,
    "ratios": process_ratios,
    "events": process_events,
    "news": process_news,
    "disclosures": process_news,
}


# ------------------------------------------------------------------ batches
def process_batch(session: Session, batch: RawBatch) -> int:
    source = get_source(batch.source)
    raw = RawStore.load(batch)
    recs = source.parse(batch.dataset, raw, batch.params or {})
    n = PROCESSORS[batch.dataset](session, recs, batch.source)
    batch.status, batch.processed_at, batch.error = "processed", utcnow(), None
    return n


def process_pending(*, batch_ids: list[str] | None = None, include_failed: bool = True,
                    dataset: str | None = None) -> dict:
    """Xử lý các batch raw chưa xử lý (và batch lỗi). Mỗi batch một transaction."""
    statuses = ["pending", "failed"] if include_failed else ["pending"]
    with session_scope() as s:
        q = select(RawBatch.id).where(RawBatch.status.in_(statuses)).order_by(RawBatch.fetched_at)
        if batch_ids:
            q = select(RawBatch.id).where(RawBatch.id.in_(batch_ids)).order_by(RawBatch.fetched_at)
        if dataset:
            q = q.where(RawBatch.dataset == dataset)
        ids = list(s.scalars(q))
    result = {"batches": 0, "records": 0, "failed": 0, "errors": []}
    for bid in ids:
        try:
            with session_scope() as s:
                n = process_batch(s, s.get(RawBatch, bid))
            result["batches"] += 1
            result["records"] += n
        except Exception as e:  # noqa: BLE001
            log.exception("Xử lý batch %s lỗi", bid)
            with session_scope() as s:
                b = s.get(RawBatch, bid)
                b.status, b.error = "failed", traceback.format_exc()[-4000:]
            result["failed"] += 1
            result["errors"].append(f"{bid}: {type(e).__name__}: {e}")
    return result


def reprocess(since: datetime | None = None, source: str | None = None, dataset: str | None = None) -> dict:
    """Đánh dấu lại batch raw là pending rồi xử lý lại, không crawl lại."""
    with session_scope() as s:
        q = update(RawBatch).values(status="pending")
        if since:
            q = q.where(RawBatch.fetched_at >= since)
        if source:
            q = q.where(RawBatch.source == source)
        if dataset:
            q = q.where(RawBatch.dataset == dataset)
        s.execute(q)
    return process_pending(dataset=dataset)


# ------------------------------------------------------------------ tagging
def build_tagger(session: Session) -> Tagger:
    cfg = get_settings().tagging
    symbols = session.execute(select(Symbol.ticker, Symbol.company_name, Symbol.short_name)).all()
    return Tagger([tuple(x) for x in symbols], aliases=cfg.get("aliases", {}),
                  ambiguous=set(cfg.get("ambiguous_tickers", [])), min_score=float(cfg.get("min_score", 0.6)))


def tag_news(all_news: bool = False, batch_size: int = 500) -> int:
    """Gắn mã cho tin chưa gắn (hoặc gắn lại tất cả)."""
    total = 0
    with session_scope() as s:
        tagger = build_tagger(s)
        if all_news:
            s.execute(delete(NewsTicker).where(NewsTicker.match_type != "source"))
            s.execute(update(News).values(tagged_at=None))
    while True:
        with session_scope() as s:
            items = s.scalars(select(News).where(News.tagged_at.is_(None)).limit(batch_size)).all()
            if not items:
                break
            rows = []
            for n in items:
                for m in tagger.tag(n.title, " ".join(filter(None, [n.summary, n.content]))):
                    rows.append({"news_id": n.id, "ticker": m.ticker, "match_type": m.match_type, "score": m.score})
                n.tagged_at = utcnow()
            ids = [n.id for n in items]
            s.execute(delete(NewsTicker).where(NewsTicker.news_id.in_(ids), NewsTicker.match_type != "source"))
            # Không ghi đè dòng "source" (mã nguồn gắn sẵn)
            existing = {(a, b) for a, b in s.execute(
                select(NewsTicker.news_id, NewsTicker.ticker).where(
                    NewsTicker.news_id.in_(ids), NewsTicker.match_type == "source"))}
            rows = [r for r in rows if (r["news_id"], r["ticker"]) not in existing]
            upsert(s, NewsTicker, rows, ["news_id", "ticker"], respect_priority=False)
            total += len(rows)
    return total
