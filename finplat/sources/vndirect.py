"""Nguồn VNDirect (finfo-api), gọi trực tiếp không qua vnstock: giá EOD và BCTC theo quý.

Endpoint lấy theo mã nguồn thư viện vnquant (phamdinhkhanh/vnquant: configs.py, loader/vnd.py, finance.py):
  - Giá:  GET https://finfo-api.vndirect.com.vn/v4/stock_prices/
          ?sort=date&q=code:VNM~date:gte:2026-09-25~date:lte:2026-10-03&size=1000&page=1
          -> {"data": [{"code","date","open","high","low","close","nmVolume","ptVolume","adClose",...}]}
  - BCTC: GET https://finfo-api.vndirect.com.vn/v3/stocks/financialStatement
          ?secCodes=VNM&reportTypes=QUARTER&modelTypes=<...>&fromDate=&toDate=
          modelTypes 1,89,101,411 = cân đối kế toán; 2,90,102,412 = kết quả kinh doanh; 3,91,103,413 = lưu chuyển
          -> {"data": {"hits": [{"secCode","fiscalDate","itemCode","itemName","numericValue","modelType",...}]}}
CHƯA GỌI THỬ được từ môi trường build (mạng chặn): tên trường có thể lệch, parse dò nhiều tên và lưu raw
nguyên bản để sửa parse rồi chạy lại từ raw. Đơn vị giá tự nhận diện (nghìn đồng/đồng); đơn vị BCTC đặt
trong [units.vndirect], kiểm tra bằng báo cáo sai lệch giữa nguồn sau lần chạy đầu.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from ..config import get_settings
from ..schemas import FinancialRec, PriceRec
from ..utils import first_present, to_float
from .base import register
from .httpbase import HttpTickerSource
from .vnstock_source import price_unit

PRICE_URL = "https://finfo-api.vndirect.com.vn/v4/stock_prices/"
FIN_URL = "https://finfo-api.vndirect.com.vn/v3/stocks/financialStatement"
MODEL_TYPES = {"BS": "1,89,101,411", "IS": "2,90,102,412", "CF": "3,91,103,413"}


class VndirectSource(HttpTickerSource):
    name = "vndirect"
    datasets = ("prices", "financials")

    def fetch(self, dataset: str, **params: Any) -> list[dict]:
        tickers: list[str] = params.get("tickers") or []
        if dataset == "prices":
            end = params.get("end") or date.today().isoformat()
            start = params.get("start") or (date.fromisoformat(end) - timedelta(days=7)).isoformat()

            def one(t: str) -> list[dict]:
                q = f"code:{t}~date:gte:{start}~date:lte:{end}"
                data = self.request_json("GET", PRICE_URL, params={"sort": "date", "q": q, "size": 1000, "page": 1})
                return list((data or {}).get("data") or [])

            return self.per_ticker(tickers, one)

        if dataset == "financials":
            years = int(params.get("years", 3))
            to_d = date.today()
            from_d = date(to_d.year - years, 1, 1)

            def one(t: str) -> list[dict]:
                out = []
                for stmt, models in MODEL_TYPES.items():
                    data = self.request_json("GET", FIN_URL, params={
                        "secCodes": t, "reportTypes": "QUARTER", "modelTypes": models,
                        "fromDate": from_d.isoformat(), "toDate": to_d.isoformat()})
                    hits = ((data or {}).get("data") or {}).get("hits") or (data or {}).get("data") or []
                    for h in hits:
                        rec = dict(h.get("_source", h))  # một số API trả dạng Elasticsearch
                        rec["_statement"] = stmt
                        out.append(rec)
                return out

            return self.per_ticker(tickers, one)
        raise ValueError(f"{self.name} không hỗ trợ dataset {dataset}")

    def parse(self, dataset: str, raw: list[dict], params: dict | None = None) -> list:
        s = get_settings()
        raw = [r for r in raw if "_error" not in r]
        if dataset == "prices":
            unit = s.unit(self.name, "price", "auto")
            out = []
            for r in raw:
                d = first_present(r, ["date", "tradingDate", "time"])
                close = to_float(first_present(r, ["close", "closePrice"]))
                if not d or close is None:
                    continue
                nm, pt = to_float(first_present(r, ["nmVolume", "volume"])), to_float(r.get("ptVolume"))
                out.append(PriceRec(
                    ticker=str(first_present(r, ["code", "ticker", "symbol"])).upper(), date=str(d)[:10],
                    open=to_float(first_present(r, ["open", "openPrice"])),
                    high=to_float(first_present(r, ["high", "highPrice"])),
                    low=to_float(first_present(r, ["low", "lowPrice"])),
                    close=close, volume=(nm or 0) + (pt or 0) if nm is not None or pt is not None else None,
                    unit=price_unit(close, unit)))
            return out

        if dataset == "financials":
            unit = s.unit(self.name, "financial", "VND")
            out = []
            for r in raw:
                name = first_present(r, ["itemName", "itemVnName", "item_name", "name"])
                value = to_float(first_present(r, ["numericValue", "value", "amount"]))
                fiscal = first_present(r, ["fiscalDate", "reportDate", "period", "date"])
                if not name or value is None or not fiscal:
                    continue
                year, quarter = _fiscal_to_period(str(fiscal))
                if year is None:
                    continue
                out.append(FinancialRec(ticker=str(first_present(r, ["secCode", "code", "ticker"])).upper(),
                                        period="", statement=r.get("_statement", "IS"), item_name=str(name),
                                        value=value, unit=unit, year=year, quarter=quarter))
            return out
        raise ValueError(f"{self.name} không hỗ trợ dataset {dataset}")


def _fiscal_to_period(fiscal: str) -> tuple[int | None, int | None]:
    """'2026-06-30' -> (2026, 2); '2026Q2'/'Q2/2026' giao cho normalize_period qua year=None."""
    try:
        d = datetime.strptime(fiscal[:10], "%Y-%m-%d").date()
    except ValueError:
        from ..processing.normalize import normalize_period

        try:
            _, y, q = normalize_period(fiscal)
            return y, q
        except ValueError:
            return None, None
    return d.year, (d.month - 1) // 3 + 1


register(VndirectSource())
