"""Nguồn số liệu tài chính qua thư viện vnstock (3.x).

Mỗi provider của vnstock (VCI, TCBS) là một nguồn riêng: vnstock_vci, vnstock_tcbs.
vnstock được import khi chạy, nên phần còn lại của hệ thống vẫn chạy được khi chưa cài.

Raw lưu đúng bảng vnstock trả về (mỗi dòng DataFrame một dict, cột MultiIndex nối bằng "|").
Tên cột thay đổi giữa các phiên bản/provider, nên parse dò theo danh sách tên có thể có.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

from ..config import get_settings
from ..schemas import EventRec, FinancialRec, PriceRec, RatioRec, SymbolRec
from ..utils import first_present, to_float
from .base import Source, register

log = logging.getLogger(__name__)

STATEMENTS = {"IS": "income_statement", "BS": "balance_sheet", "CF": "cash_flow"}

# Tên cột có thể gặp (đã lower-case) — VCI lang=vi / lang=en, TCBS
TICKER_COLS = ["ticker", "cp", "symbol", "meta|cp", "meta|ticker"]
YEAR_COLS = ["year", "yearreport", "năm", "nam", "meta|năm", "meta|yearreport"]
QUARTER_COLS = ["quarter", "lengthreport", "kỳ", "ky", "meta|kỳ", "meta|lengthreport"]
META_COLS = set(TICKER_COLS + YEAR_COLS + QUARTER_COLS + ["meta|kỳ báo cáo", "period", "kỳ báo cáo"])

RATIO_ALIASES = {
    "pe": ["p/e", "pe", "price_to_earning", "price to earning"],
    "pb": ["p/b", "pb", "price_to_book", "price to book"],
    "roe": ["roe (%)", "roe", "roe(%)"],
    "eps": ["eps (vnd)", "eps", "earning_per_share", "eps(vnd)"],
}


def _df_records(df) -> list[dict]:
    """DataFrame -> list[dict], an toàn với cột MultiIndex và NaN."""
    if df is None:
        return []
    df = df.copy()
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = ["|".join(str(x) for x in col if str(x) != "") for col in df.columns]
    else:
        df.columns = [str(c) for c in df.columns]
    df = df.reset_index(drop=df.index.name is None)
    df = df.astype(object).where(df.notna(), None)
    return df.to_dict(orient="records")


def _last_segment(col: str) -> str:
    return col.split("|")[-1].strip().lower()


class VnstockSource(Source):
    datasets = ("symbols", "prices", "financials", "ratios", "events")

    def __init__(self, provider: str):
        self.provider = provider.upper()
        self.name = f"vnstock_{provider.lower()}"
        self._lang = "vi"

    # ------------------------------------------------------------------ fetch
    def _vnstock(self):
        try:
            import vnstock  # type: ignore
        except ImportError as e:  # pragma: no cover - phụ thuộc môi trường
            raise RuntimeError("Chưa cài vnstock: pip install vnstock") from e
        return vnstock

    def _stock(self, ticker: str):
        return self._vnstock().Vnstock().stock(symbol=ticker, source=self.provider)

    def _per_ticker(self, tickers: list[str], fn) -> list[dict]:
        delay = float(get_settings().general("request_delay", 0.4))
        out: list[dict] = []
        for t in tickers:
            try:
                for rec in fn(t):
                    rec.setdefault("ticker", t)
                    out.append(rec)
            except Exception as e:  # lỗi một mã không làm hỏng cả batch
                log.warning("%s: lỗi %s: %s", self.name, t, e)
                out.append({"ticker": t, "_error": f"{type(e).__name__}: {e}"})
            if delay:
                time.sleep(delay)
        return out

    def fetch(self, dataset: str, **params: Any) -> list[dict]:
        tickers: list[str] = params.get("tickers") or []
        if dataset == "symbols":
            vn = self._vnstock()
            listing = vn.Listing(source=self.provider) if self.provider == "VCI" else vn.Listing()
            rows = _df_records(listing.symbols_by_exchange())
            try:  # bổ sung ngành nếu provider hỗ trợ
                ind = {r.get("symbol"): r for r in _df_records(listing.symbols_by_industries())}
                for r in rows:
                    extra = ind.get(r.get("symbol")) or {}
                    r.setdefault("industry", extra.get("icb_name3") or extra.get("icb_name2"))
            except Exception as e:  # noqa: BLE001
                log.info("Không lấy được ngành: %s", e)
            return rows

        if dataset == "prices":
            end = params.get("end") or date.today().isoformat()
            start = params.get("start") or (date.fromisoformat(end) - timedelta(days=7)).isoformat()
            return self._per_ticker(
                tickers,
                lambda t: _df_records(self._stock(t).quote.history(start=start, end=end, interval="1D")),
            )

        if dataset == "financials":
            def one(t):
                fin = self._stock(t).finance
                out = []
                for code, method in STATEMENTS.items():
                    for r in _df_records(getattr(fin, method)(period="quarter", lang=self._lang, dropna=True)):
                        r["_statement"] = code
                        out.append(r)
                return out

            return self._per_ticker(tickers, one)

        if dataset == "ratios":
            return self._per_ticker(
                tickers,
                lambda t: _df_records(self._stock(t).finance.ratio(period="quarter", lang=self._lang, dropna=True)),
            )

        if dataset == "events":
            def one(t):
                company = self._stock(t).company
                out = []
                for kind in ("dividends", "events"):
                    if hasattr(company, kind):
                        try:
                            for r in _df_records(getattr(company, kind)()):
                                r["_kind"] = kind
                                out.append(r)
                        except Exception as e:  # noqa: BLE001
                            log.info("%s %s.%s: %s", self.name, t, kind, e)
                return out

            return self._per_ticker(tickers, one)

        raise ValueError(f"{self.name} không hỗ trợ dataset {dataset}")

    # ------------------------------------------------------------------ parse
    def parse(self, dataset: str, raw: list[dict], params: dict | None = None) -> list:
        s = get_settings()
        raw = [r for r in raw if "_error" not in r]
        if dataset == "symbols":
            out = []
            for r in raw:
                ticker = first_present(r, ["symbol", "ticker"])
                if not ticker:
                    continue
                out.append(
                    SymbolRec(
                        ticker=str(ticker).upper(),
                        exchange=_norm_exchange(first_present(r, ["exchange", "board", "comGroupCode"])),
                        company_name=first_present(r, ["organ_name", "organName", "company_name"]),
                        short_name=first_present(r, ["organ_short_name", "organShortName", "short_name"]),
                        industry=r.get("industry"),
                        type=first_present(r, ["type"]),
                    )
                )
            return out

        if dataset == "prices":
            unit = s.unit(self.name, "price", "auto")
            return [
                PriceRec(
                    ticker=r["ticker"],
                    date=str(first_present(r, ["time", "date", "tradingdate"]))[:10],
                    open=to_float(r.get("open")),
                    high=to_float(r.get("high")),
                    low=to_float(r.get("low")),
                    close=to_float(r.get("close")),
                    volume=to_float(r.get("volume")),
                    unit=price_unit(to_float(r.get("close")), unit),
                )
                for r in raw
                if first_present(r, ["time", "date", "tradingdate"])
            ]

        if dataset == "financials":
            unit = s.unit(self.name, "financial", "VND")
            out = []
            for r in raw:
                lower = {k.lower(): k for k in r}
                year = first_present(r, YEAR_COLS)
                quarter = first_present(r, QUARTER_COLS)
                if year is None:
                    continue
                for low, orig in lower.items():
                    if low in META_COLS or low.startswith("_"):
                        continue
                    value = to_float(r[orig])
                    if value is None:
                        continue
                    out.append(
                        FinancialRec(
                            ticker=r["ticker"],
                            period="",
                            statement=r.get("_statement", "IS"),
                            item_name=orig.split("|")[-1].strip(),
                            value=value,
                            unit=unit,
                            year=int(to_float(year)),
                            quarter=int(to_float(quarter) or 0),
                        )
                    )
            return out

        if dataset == "ratios":
            eps_unit = s.unit(self.name, "eps", "VND")
            out = []
            for r in raw:
                year = first_present(r, YEAR_COLS)
                if year is None:
                    continue
                by_name = {_last_segment(k): v for k, v in r.items()}
                vals = {
                    field: to_float(next((by_name[a] for a in aliases if a in by_name), None))
                    for field, aliases in RATIO_ALIASES.items()
                }
                out.append(
                    RatioRec(
                        ticker=r["ticker"],
                        period="",
                        year=int(to_float(year)),
                        quarter=int(to_float(first_present(r, QUARTER_COLS)) or 0),
                        eps_unit=eps_unit,
                        **vals,
                    )
                )
            return out

        if dataset == "events":
            return [e for r in raw if (e := _parse_event(r))]

        raise ValueError(f"{self.name} không hỗ trợ dataset {dataset}")


def price_unit(close: float | None, configured: str) -> str:
    """Đơn vị giá của một dòng. "auto": vnstock trả nghìn đồng (vd 60.5) hay đồng (60500) tuỳ provider/phiên bản;
    giá cổ phiếu VN nằm trong khoảng ~1.000-1.000.000 đ nên raw > 1000 chắc chắn là đồng, ngược lại là nghìn đồng."""
    if configured != "auto":
        return configured
    if close is None:
        return "kVND"
    return "VND" if close > 1000 else "kVND"


def _norm_exchange(value) -> str | None:
    if not value:
        return None
    v = str(value).upper()
    return {"HSX": "HOSE", "UPCOM": "UPCOM", "UPC": "UPCOM"}.get(v, v)


def _parse_event(r: dict) -> EventRec | None:
    ticker = r.get("ticker")
    if r.get("_kind") == "dividends":
        method = str(first_present(r, ["issue_method", "method"]) or "").lower()
        pct = to_float(first_present(r, ["cash_dividend_percentage", "ratio", "percentage"]))
        when = first_present(r, ["exercise_date", "exer_date", "record_date"])
        year = first_present(r, ["cash_year", "year"])
        if "share" in method or "cổ phiếu" in method:
            return EventRec(ticker, "dividend_stock", _date_str(when), (pct or 0) * 100 if pct and pct < 1 else pct,
                            "%", f"Cổ tức bằng cổ phiếu năm {year}")
        # Mệnh giá 10.000đ: 15% = 1.500đ/cp
        value = pct * 10_000 if pct is not None and pct < 5 else pct
        return EventRec(ticker, "dividend_cash", _date_str(when), value, "VND", f"Cổ tức tiền mặt năm {year}")

    title = str(first_present(r, ["event_title", "event_name", "eventname", "title", "event_list_name"]) or "")
    if not title:
        return None
    low = title.lower()
    if "cổ tức" in low or "dividend" in low:
        etype = "dividend_stock" if ("cổ phiếu" in low or "stock" in low) else "dividend_cash"
    elif "phát hành" in low or "niêm yết bổ sung" in low or "issue" in low or "additional" in low:
        etype = "issuance"
    else:
        etype = "other"
    when = first_present(r, ["exright_date", "ex_date", "exer_date", "record_date", "issue_date", "notify_date", "public_date"])
    value = to_float(first_present(r, ["value", "ratio", "price_change_ratio"]))
    return EventRec(ticker, etype, _date_str(when), value, None, title)


def _date_str(v) -> str | None:
    return str(v)[:10] if v else None


register(VnstockSource("VCI"))
register(VnstockSource("TCBS"))
