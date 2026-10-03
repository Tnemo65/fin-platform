"""Nguồn DEMO: dữ liệu GIẢ LẬP để chạy thử UI/API khi chưa có mạng tới nguồn thật.

Chỉ chạy khi gọi `python -m finplat seed-demo`. Mọi dòng mang source="demo" (ưu tiên thấp
nhất), nên dữ liệu thật từ vnstock/RSS sẽ tự ghi đè. Tin demo có tiền tố [DEMO].
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta
from typing import Any

from ..schemas import EventRec, FinancialRec, NewsRec, PriceRec, RatioRec, SymbolRec
from .base import Source, register

COMPANIES = {
    "VNM": ("HOSE", "Công ty Cổ phần Sữa Việt Nam", "Vinamilk", 65.0),
    "FPT": ("HOSE", "Công ty Cổ phần FPT", "FPT Corp", 120.0),
    "HPG": ("HOSE", "Công ty Cổ phần Tập đoàn Hòa Phát", "Hòa Phát", 27.0),
    "VCB": ("HOSE", "Ngân hàng TMCP Ngoại thương Việt Nam", "Vietcombank", 90.0),
    "SHS": ("HNX", "Công ty Cổ phần Chứng khoán Sài Gòn - Hà Nội", "Chứng khoán SHS", 15.0),
}


class DemoSource(Source):
    name = "demo"
    datasets = ("symbols", "prices", "financials", "ratios", "events", "news")

    def fetch(self, dataset: str, **params: Any) -> list[dict]:
        rnd = random.Random(42)
        today = date.fromisoformat(params.get("today") or date.today().isoformat())
        if dataset == "symbols":
            return [
                {"ticker": t, "exchange": ex, "company_name": n, "short_name": sn}
                for t, (ex, n, sn, _) in COMPANIES.items()
            ]
        if dataset == "prices":
            out = []
            for t, (*_, base) in COMPANIES.items():
                price = base
                d = today - timedelta(days=400)
                while d <= today:
                    if d.weekday() < 5:
                        o = price
                        price = max(1.0, price * (1 + rnd.gauss(0.0004, 0.018)))
                        hi, lo = max(o, price) * (1 + rnd.random() * 0.01), min(o, price) * (1 - rnd.random() * 0.01)
                        out.append({"ticker": t, "date": d.isoformat(), "open": round(o, 2), "high": round(hi, 2),
                                    "low": round(lo, 2), "close": round(price, 2),
                                    "volume": int(rnd.uniform(0.5, 3) * 1e6)})
                    d += timedelta(days=1)
            return out  # đơn vị nghìn đồng, như vnstock
        if dataset in ("financials", "ratios"):
            out = []
            for i, t in enumerate(COMPANIES):
                scale = (i + 1) * 1e12
                for k in range(12):
                    y, q = today.year - 3 + (k // 4), k % 4 + 1
                    if (y, q) >= (today.year, (today.month - 1) // 3 + 1):
                        break
                    g = 1 + 0.03 * k + 0.08 * math.sin(k + i)
                    rev = scale * g
                    out.append({"ticker": t, "year": y, "quarter": q, "revenue_bn": rev / 1e9,
                                "net_profit_bn": rev * 0.12 * (1 + rnd.uniform(-0.2, 0.2)) / 1e9,
                                "total_assets_bn": scale * 8 * g / 1e9, "equity_bn": scale * 3 * g / 1e9,
                                "cfo_bn": rev * 0.1 * rnd.uniform(0.5, 1.5) / 1e9,
                                "pe": rnd.uniform(8, 20), "pb": rnd.uniform(1, 4), "roe_pct": rnd.uniform(10, 25),
                                "eps": rnd.uniform(2000, 6000)})
            return out
        if dataset == "events":
            return [{"ticker": t, "type": "dividend_cash", "date": f"{today.year}-07-15", "value": 1500}
                    for t in COMPANIES]
        if dataset == "news":
            out = []
            for i, (t, (_, _, sn, _)) in enumerate(COMPANIES.items()):
                for k in range(3):
                    ts = datetime.combine(today, datetime.min.time()) - timedelta(hours=7 * (i + k * 5))
                    out.append({"url": f"https://demo.invalid/{t.lower()}-{k}",
                                "title": f"[DEMO] {sn} ({t}) công bố kết quả kinh doanh quý, tin số {k + 1}",
                                "published": ts.isoformat(),
                                "content": f"Tin giả lập về {sn}. Cổ phiếu {t} được nhắc trong bài."})
            # Một tin đăng lại (cùng tiêu đề, khác URL) để thử khử trùng
            out.append({**out[0], "url": "https://demo-mirror.invalid/republish"})
            return out
        raise ValueError(dataset)

    def parse(self, dataset: str, raw: list[dict], params: dict | None = None) -> list:
        if dataset == "symbols":
            return [SymbolRec(r["ticker"], r["exchange"], r["company_name"], r["short_name"]) for r in raw]
        if dataset == "prices":
            return [PriceRec(r["ticker"], r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"], "kVND")
                    for r in raw]
        if dataset == "financials":
            items = {"revenue_bn": ("IS", "Doanh thu thuần"), "net_profit_bn": ("IS", "Lợi nhuận sau thuế"),
                     "total_assets_bn": ("BS", "Tổng cộng tài sản"), "equity_bn": ("BS", "Vốn chủ sở hữu"),
                     "cfo_bn": ("CF", "Lưu chuyển tiền thuần từ hoạt động kinh doanh")}
            return [FinancialRec(r["ticker"], f"Q{r['quarter']}/{r['year']}", st, name, r[k], "bVND")
                    for r in raw for k, (st, name) in items.items()]
        if dataset == "ratios":
            return [RatioRec(r["ticker"], f"{r['year']}-Q{r['quarter']}", r["pe"], r["pb"], r["roe_pct"], r["eps"])
                    for r in raw]
        if dataset == "events":
            return [EventRec(r["ticker"], r["type"], r["date"], r["value"], "VND", "[DEMO] Cổ tức tiền mặt")
                    for r in raw]
        if dataset == "news":
            return [NewsRec(r["url"], r["title"], r["published"], content=r["content"]) for r in raw]
        raise ValueError(dataset)


register(DemoSource())
