"""Nhiều nguồn cùng lúc: mọi nguồn đều được crawl, giữ nguyên giá trị từng nguồn, đo độ phủ và sai lệch."""
from datetime import date

from sqlalchemy import select

from finplat import jobs
from finplat.checks import financial_disagreements, price_disagreements, run_checks, source_coverage
from finplat.db import session_scope
from finplat.models import FinancialItem, PriceDaily, PriceDailySource, RawBatch
from finplat.processing import pipeline
from finplat.schemas import FinancialRec, PriceRec
from finplat.sources import base
from finplat.sources.base import Source


def _price(ticker, close, d="2026-10-02"):
    return PriceRec(ticker, d, close, close, close, close, 1000, "kVND")


def test_every_source_is_kept_and_core_follows_priority():
    with session_scope() as s:
        pipeline.process_prices(s, [_price("VNM", 83.4)], "vnstock_vci")
        pipeline.process_prices(s, [_price("VNM", 83.5)], "vndirect")
        pipeline.process_prices(s, [_price("VNM", 83.42)], "cafef_prices")
    with session_scope() as s:
        by_source = {r.source: r.close for r in s.scalars(select(PriceDailySource))}
        assert by_source == {"vnstock_vci": 83_400, "vndirect": 83_500, "cafef_prices": 83_420}  # không mất nguồn nào
        core = s.scalars(select(PriceDaily)).one()
        assert core.source == "vnstock_vci" and core.close == 83_400  # core theo [priority]


def test_all_enabled_sources_are_crawled_in_one_job(monkeypatch):
    seen = []

    class Fake(Source):
        datasets = ("prices",)

        def __init__(self, name, close):
            self.name, self.close = name, close

        def fetch(self, dataset, **params):
            seen.append(self.name)
            # nguồn B thiếu 1 mã, để kiểm tra độ phủ
            tickers = params["tickers"] if self.name != "src_b" else params["tickers"][:-1]
            return [{"ticker": t, "date": "2026-10-02", "close": self.close} for t in tickers] + (
                [{"ticker": params["tickers"][-1], "_error": "HTTP 500"}] if self.name == "src_b" else [])

        def parse(self, dataset, raw, params=None):
            return [PriceRec(r["ticker"], r["date"], r["close"], r["close"], r["close"], r["close"], 1, "kVND")
                    for r in raw if "_error" not in r]

    for n, c in (("src_a", 50.0), ("src_b", 50.2), ("src_c", 52.0)):
        monkeypatch.setitem(base._REGISTRY, n, Fake(n, c))
    from finplat.config import get_settings

    get_settings().raw["sources"]["prices"] = ["src_a", "src_b", "src_c"]
    get_settings().raw["priority"]["prices"] = ["src_a", "src_b", "src_c"]
    monkeypatch.setattr(jobs, "today_vn", lambda: date(2026, 10, 2))

    jobs.job_prices_eod(tickers=["AAA", "BBB", "CCC", "DDD"], force=True)

    assert sorted(seen) == ["src_a", "src_b", "src_c"]  # không nguồn nào bị bỏ qua
    with session_scope() as s:
        assert s.scalar(select(PriceDailySource.source).distinct().where(PriceDailySource.ticker == "AAA")) is not None
        assert {r.source for r in s.scalars(select(PriceDailySource).where(PriceDailySource.ticker == "AAA"))} == {
            "src_a", "src_b", "src_c"}
        # độ phủ ghi trên raw_batches
        cov = {c["source"]: c for c in source_coverage(s)}
        assert cov["src_a"]["ok"] == 4 and cov["src_a"]["requested"] == 4 and cov["src_a"]["coverage"] == 1.0
        assert cov["src_b"]["ok"] == 3 and cov["src_b"]["errors"] == 1 and cov["src_b"]["coverage"] == 0.75
        # sai lệch: src_c lệch 4% so với src_a/src_b -> báo cho cả 4 mã; src_a vs src_b chỉ 0.4% -> không
        dis = price_disagreements(s, date(2026, 10, 2), 1.0)
        assert [d["ticker"] for d in dis] == ["AAA", "BBB", "CCC", "DDD"] and dis[0]["values"]["src_c"] == 52_000
        assert s.scalars(select(RawBatch.status)).all() == ["processed"] * 3

    report = run_checks(notify=False)
    assert any("src_b/prices" in p and "3/4" in p for p in report.problems)  # nguồn thiếu mã bị báo
    assert any("lệch giữa các nguồn" in p for p in report.problems)


def test_financial_disagreement_catches_unit_mismatch():
    with session_scope() as s:
        pipeline.process_financials(s, [FinancialRec("FPT", "Q2/2026", "IS", "Doanh thu thuần", 1.5e13, "VND")], "vnstock_vci")
        pipeline.process_financials(s, [FinancialRec("FPT", "Q2/2026", "IS", "Doanh thu thuần", 1.5e4, "VND")], "vndirect")
        pipeline.process_financials(s, [FinancialRec("FPT", "Q2/2026", "IS", "Doanh thu thuần", 1.51e13, "VND")], "vnstock_kbs")
    with session_scope() as s:
        (d,) = financial_disagreements(s, 2.0)
        assert d["item_code"] == "revenue" and set(d["values"]) == {"vnstock_vci", "vndirect", "vnstock_kbs"}
        assert s.scalars(select(FinancialItem)).one().source == "vnstock_vci"


def test_vndirect_and_cafef_parse_documented_shapes():
    from finplat.sources.cafef import CafefPriceSource
    from finplat.sources.vndirect import VndirectSource

    vnd = VndirectSource()
    (p,) = vnd.parse("prices", [{"ticker": "VNM", "code": "VNM", "date": "2026-10-02", "open": 83.0, "high": 84.1,
                                "low": 82.9, "close": 83.4, "nmVolume": 1_000_000, "ptVolume": 50_000},
                               {"ticker": "XXX", "_error": "HTTPError"}])
    assert (p.date, p.close, p.volume, p.unit) == ("2026-10-02", 83.4, 1_050_000, "kVND")
    fins = vnd.parse("financials", [
        {"ticker": "VNM", "secCode": "VNM", "fiscalDate": "2026-06-30", "itemName": "Doanh thu thuần",
         "numericValue": 1.6e13, "_statement": "IS"},
        {"ticker": "VNM", "secCode": "VNM", "fiscalDate": "2026-06-30", "itemName": "Tổng cộng tài sản",
         "numericValue": 5e13, "_statement": "BS"},
    ])
    assert [(f.statement, f.year, f.quarter) for f in fins] == [("IS", 2026, 2), ("BS", 2026, 2)]

    cf = CafefPriceSource()
    (c,) = cf.parse("prices", [{"ticker": "VNM", "Ngay": "02/10/2026", "GiaDongCua": 83.42, "GiaMoCua": 83.0,
                               "GiaCaoNhat": 84.0, "GiaThapNhat": 82.9, "KhoiLuongKhopLenh": 900_000, "KLThoaThuan": 100_000}])
    assert (c.date, c.close, c.volume, c.unit) == ("2026-10-02", 83.42, 1_000_000, "kVND")

    with session_scope() as s:
        pipeline.process_prices(s, [p], "vndirect")
        pipeline.process_prices(s, [c], "cafef_prices")
    with session_scope() as s:
        assert {r.source: r.close for r in s.scalars(select(PriceDailySource))} == {"vndirect": 83_400, "cafef_prices": 83_420}


def test_http_sources_record_per_ticker_errors_without_network(monkeypatch):
    from finplat.sources.vndirect import VndirectSource

    src = VndirectSource()
    monkeypatch.setattr(src, "request_json", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")))
    raw = src.fetch("prices", tickers=["VNM", "FPT"], start="2026-09-25", end="2026-10-02")
    assert [r["ticker"] for r in raw] == ["VNM", "FPT"] and all("_error" in r for r in raw)
    assert src.parse("prices", raw) == []
