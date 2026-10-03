from __future__ import annotations

"""FastAPI. Chạy: python -m finplat api [--workers N]  (hoặc uvicorn finplat.api.main:app --workers N)

Hiệu năng: endpoint là hàm sync nên FastAPI chạy chúng trên thread pool (API_THREADS luồng/worker,
mặc định 64), nhiều request được phục vụ cùng lúc; `--workers` nhân thêm theo số tiến trình.
Phản hồi lớn (giá nhiều năm) được nén gzip.
"""
import os
from contextlib import asynccontextmanager
from datetime import date

import anyio
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..checks import data_status
from ..db import get_sessionmaker, init_db
from ..models import CorporateEvent, FinancialItem, News, NewsTicker, PriceDaily, PriceDailySource, Ratio, Symbol
from ..processing.normalize import KEY_ITEMS, prev_year_period


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    # Số luồng chạy endpoint sync đồng thời trong một worker (mặc định của anyio là 40)
    anyio.to_thread.current_default_thread_limiter().total_tokens = int(os.environ.get("API_THREADS", "64"))
    yield


app = FastAPI(title="Finance Platform API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1024)

STATEMENT_NAMES = {"IS": "Kết quả kinh doanh", "BS": "Cân đối kế toán", "CF": "Lưu chuyển tiền tệ"}


def get_db():
    s = get_sessionmaker()()
    try:
        yield s
    finally:
        s.close()


def _symbol_or_404(db: Session, ticker: str) -> Symbol:
    sym = db.get(Symbol, ticker.upper())
    if not sym:
        raise HTTPException(404, f"Không có mã {ticker.upper()}")
    return sym


def _sym_dict(s: Symbol) -> dict:
    return {"ticker": s.ticker, "exchange": s.exchange, "company_name": s.company_name,
            "short_name": s.short_name, "industry": s.industry}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/symbols")
def list_symbols(q: str | None = None, limit: int = Query(20, le=200), db: Session = Depends(get_db)):
    stmt = select(Symbol).where(Symbol.is_active.is_(True))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Symbol.ticker.ilike(like), Symbol.company_name.ilike(like), Symbol.short_name.ilike(like)))
        # mã khớp chính xác/đầu mã lên trước
        stmt = stmt.order_by((Symbol.ticker != q.strip().upper()), Symbol.ticker)
    else:
        stmt = stmt.order_by(Symbol.ticker)
    return [_sym_dict(s) for s in db.scalars(stmt.limit(limit))]


@app.get("/symbols/{ticker}")
def get_symbol(ticker: str, db: Session = Depends(get_db)):
    return _sym_dict(_symbol_or_404(db, ticker))


@app.get("/symbols/{ticker}/prices")
def get_prices(ticker: str, start: date | None = None, end: date | None = None,
               limit: int = Query(1000, le=10000), source: str | None = None, db: Session = Depends(get_db)):
    """Giá EOD (VND), sắp xếp theo ngày tăng dần. Mặc định là bản hợp nhất theo ưu tiên nguồn;
    `source=` trả đúng dữ liệu của một nguồn (vnstock_vci, vndirect, cafef_prices...)."""
    _symbol_or_404(db, ticker)
    model = PriceDailySource if source else PriceDaily
    stmt = select(model).where(model.ticker == ticker.upper())
    if source:
        stmt = stmt.where(model.source == source)
    if start:
        stmt = stmt.where(model.date >= start)
    if end:
        stmt = stmt.where(model.date <= end)
    rows = list(db.scalars(stmt.order_by(model.date.desc()).limit(limit)))[::-1]
    return {"ticker": ticker.upper(), "unit": "VND", "prices": [
        {"date": r.date.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
         "volume": r.volume, "source": r.source} for r in rows]}


@app.get("/symbols/{ticker}/prices/compare")
def compare_prices(ticker: str, limit: int = Query(30, le=500), db: Session = Depends(get_db)):
    """Giá đóng cửa theo từng nguồn, cùng ngày đặt cạnh nhau, để thấy nguồn nào lệch."""
    t = ticker.upper()
    _symbol_or_404(db, t)
    days = list(db.scalars(select(PriceDailySource.date).where(PriceDailySource.ticker == t).distinct()
                           .order_by(PriceDailySource.date.desc()).limit(limit)))
    rows = db.execute(select(PriceDailySource.date, PriceDailySource.source, PriceDailySource.close)
                      .where(PriceDailySource.ticker == t, PriceDailySource.date.in_(days))).all()
    by_day: dict[str, dict[str, float | None]] = {}
    for d, src, close in rows:
        by_day.setdefault(d.isoformat(), {})[src] = close
    return {"ticker": t, "sources": sorted({src for _, src, _ in rows}),
            "days": [{"date": d, "close": vals} for d, vals in sorted(by_day.items())]}


@app.get("/symbols/{ticker}/financials")
def get_financials(ticker: str, periods: int = Query(8, ge=1, le=40), statement: str | None = None,
                   key_only: bool = False, db: Session = Depends(get_db)):
    """BCTC theo quý (VND) + so sánh cùng kỳ năm trước (yoy_pct) + chỉ số P/E, P/B, ROE, EPS."""
    t = ticker.upper()
    _symbol_or_404(db, t)
    recent = list(db.scalars(
        select(FinancialItem.period).where(FinancialItem.ticker == t, FinancialItem.quarter > 0)
        .distinct().order_by(FinancialItem.period.desc()).limit(periods)))
    wanted = set(recent) | {prev_year_period(p) for p in recent}
    stmt = select(FinancialItem).where(FinancialItem.ticker == t, FinancialItem.period.in_(wanted))
    if statement:
        stmt = stmt.where(FinancialItem.statement == statement.upper())
    if key_only:
        stmt = stmt.where(FinancialItem.item_code.in_(KEY_ITEMS))
    values = {(r.statement, r.item_code, r.period): r for r in db.scalars(stmt)}

    items = []
    for (st, code, period), r in values.items():
        if period not in recent:
            continue
        prev = values.get((st, code, prev_year_period(period)))
        yoy = None
        if prev and prev.value not in (None, 0) and r.value is not None:
            yoy = (r.value - prev.value) / abs(prev.value)
        items.append({"statement": st, "item_code": code, "item_name": r.item_name, "period": period,
                      "value": r.value, "prev_year_value": prev.value if prev else None,
                      "yoy_pct": round(yoy * 100, 2) if yoy is not None else None})
    order = {c: i for i, c in enumerate(KEY_ITEMS)}
    items.sort(key=lambda x: (x["statement"], order.get(x["item_code"], 999), x["item_code"], x["period"]))

    ratios = db.scalars(select(Ratio).where(Ratio.ticker == t, Ratio.period.in_(recent)).order_by(Ratio.period)).all()
    return {
        "ticker": t, "unit": "VND", "periods": sorted(recent), "statements": STATEMENT_NAMES, "items": items,
        "ratios": [{"period": r.period, "pe": r.pe, "pb": r.pb, "roe": r.roe, "eps": r.eps} for r in ratios],
    }


@app.get("/symbols/{ticker}/news")
def get_news(ticker: str, limit: int = Query(50, le=200), offset: int = 0, db: Session = Depends(get_db)):
    """Timeline tin của mã, mới nhất trước."""
    t = ticker.upper()
    _symbol_or_404(db, t)
    stmt = (select(News, NewsTicker.match_type, NewsTicker.score).join(NewsTicker, NewsTicker.news_id == News.id)
            .where(NewsTicker.ticker == t)
            .order_by(func.coalesce(News.published_at, News.created_at).desc()).offset(offset).limit(limit))
    rows = db.execute(stmt).all()
    # Mã khác cùng được nhắc trong các tin này: một truy vấn cho cả trang thay vì mỗi tin một truy vấn
    others: dict[str, list[str]] = {}
    if rows:
        for nid, other in db.execute(select(NewsTicker.news_id, NewsTicker.ticker).where(
                NewsTicker.news_id.in_([n.id for n, _, _ in rows]), NewsTicker.ticker != t).order_by(NewsTicker.ticker)):
            others.setdefault(nid, []).append(other)
    out = []
    for n, mt, score in rows:
        out.append({"id": n.id, "title": n.title, "url": n.url, "source": n.source, "kind": n.kind,
                    "published_at": n.published_at.isoformat() + "Z" if n.published_at else None,
                    "summary": n.summary or (n.content[:300] if n.content else None),
                    "match_type": mt, "score": score, "other_tickers": others.get(n.id, [])})
    return {"ticker": t, "news": out}


@app.get("/symbols/{ticker}/events")
def get_events(ticker: str, db: Session = Depends(get_db)):
    t = ticker.upper()
    _symbol_or_404(db, t)
    rows = db.scalars(select(CorporateEvent).where(CorporateEvent.ticker == t)
                      .order_by(CorporateEvent.event_date.desc())).all()
    return {"ticker": t, "events": [
        {"event_type": r.event_type, "event_date": r.event_date.isoformat() if r.event_date else None,
         "value": r.value, "unit": r.unit, "description": r.description, "source": r.source} for r in rows]}


@app.get("/status")
def status(days: int = 7):
    """Lần cập nhật gần nhất của từng nguồn và các job lỗi."""
    return data_status(days)
