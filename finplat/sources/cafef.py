"""Nguồn giá EOD từ CafeF, gọi trực tiếp (không phụ thuộc vnstock).

Endpoint theo mã nguồn vnquant (loader/cafe.py, configs.py):
  https://s.cafef.vn/Ajax/PageNew/DataHistory/PriceHistory.ashx
  tham số Symbol, StartDate (dd/mm/yyyy), EndDate, PageIndex, PageSize
  -> {"Success": true, "Data": {"TotalCount": n, "Data": [{"Ngay": "02/10/2026", "GiaDongCua": 83.42,
      "GiaMoCua":..., "GiaCaoNhat":..., "GiaThapNhat":..., "GiaDieuChinh":..., "KhoiLuongKhopLenh":...,
      "KLThoaThuan":..., "GiaTriKhopLenh":..., "GtThoaThuan":...}]}}
CHƯA GỌI THỬ được từ môi trường build. vnquant gửi form POST; ở đây thử GET trước, lỗi thì POST.
Giá CafeF hiển thị theo nghìn đồng, parse tự nhận diện đơn vị. Tên nguồn là "cafef_prices" vì "cafef"
đã là nguồn RSS tin tức.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..config import get_settings
from ..schemas import PriceRec
from ..utils import first_present, to_float
from .base import register
from .httpbase import HttpTickerSource
from .vnstock_source import price_unit

URL = "https://s.cafef.vn/Ajax/PageNew/DataHistory/PriceHistory.ashx"


def _ddmmyyyy(iso: str) -> str:
    y, m, d = iso[:10].split("-")
    return f"{d}/{m}/{y}"


class CafefPriceSource(HttpTickerSource):
    name = "cafef_prices"
    datasets = ("prices",)

    def fetch(self, dataset: str, **params: Any) -> list[dict]:
        if dataset != "prices":
            raise ValueError(f"{self.name} không hỗ trợ dataset {dataset}")
        tickers: list[str] = params.get("tickers") or []
        end = params.get("end") or date.today().isoformat()
        start = params.get("start") or (date.fromisoformat(end) - timedelta(days=7)).isoformat()
        form = {"StartDate": _ddmmyyyy(start), "EndDate": _ddmmyyyy(end), "PageIndex": 1, "PageSize": 1000}

        def one(t: str) -> list[dict]:
            payload = {"Symbol": t, **form}
            try:
                data = self.request_json("GET", URL, params=payload)
            except Exception:  # noqa: BLE001 - một số bản CafeF chỉ nhận POST form
                data = self.request_json("POST", URL, data=payload,
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            if isinstance(data, dict) and data.get("Success") is False:
                raise RuntimeError(f"CafeF trả Success=false cho {t}")
            inner = (data or {}).get("Data") if isinstance(data, dict) else None
            rows = inner.get("Data") if isinstance(inner, dict) else inner
            return [dict(r) for r in (rows or [])]

        return self.per_ticker(tickers, one)

    def parse(self, dataset: str, raw: list[dict], params: dict | None = None) -> list[PriceRec]:
        unit = get_settings().unit(self.name, "price", "auto")
        out = []
        for r in raw:
            if "_error" in r:
                continue
            d = first_present(r, ["Ngay", "date"])
            close = to_float(first_present(r, ["GiaDongCua", "close"]))
            if not d or close is None:
                continue
            iso = d if "-" in str(d) else "-".join(reversed(str(d)[:10].split("/")))
            kl = to_float(first_present(r, ["KhoiLuongKhopLenh", "volume"]))
            tt = to_float(r.get("KLThoaThuan"))
            out.append(PriceRec(ticker=str(r["ticker"]).upper(), date=iso,
                                open=to_float(r.get("GiaMoCua")), high=to_float(r.get("GiaCaoNhat")),
                                low=to_float(r.get("GiaThapNhat")), close=close,
                                volume=(kl or 0) + (tt or 0) if kl is not None or tt is not None else None,
                                unit=price_unit(close, unit)))
        return out


register(CafefPriceSource())
