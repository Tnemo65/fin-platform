from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import func, select

from finplat.db import session_scope, upsert
from finplat.models import FinancialItem, News, NewsTicker, PriceDaily, RawBatch, Ratio, Symbol
from finplat.processing import pipeline
from finplat.processing.normalize import (
    canonical_url,
    item_code,
    normalize_period,
    title_hash,
    to_vnd,
    unit_from_name,
    url_hash,
)
from finplat.processing.tagger import Tagger
from finplat.schemas import FinancialRec, NewsRec, PriceRec, RatioRec, SymbolRec

FIX = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------------ chuẩn hoá
@pytest.mark.parametrize("raw,expected", [
    ("2026Q2", "2026Q2"), ("2026-Q2", "2026Q2"), ("Q2/2026", "2026Q2"), ("Q2 2026", "2026Q2"),
    ("Quý 2/2026", "2026Q2"), ("Quý II năm 2026", "2026Q2"), ("quý IV/2025", "2025Q4"), ("2025", "2025Y"),
])
def test_normalize_period(raw, expected):
    assert normalize_period(raw)[0] == expected


def test_normalize_period_from_parts():
    assert normalize_period(None, 2026, 3) == ("2026Q3", 2026, 3)
    assert normalize_period(None, 2025, 5) == ("2025Y", 2025, 0)


def test_units():
    assert to_vnd(25.5, "kVND") == 25_500
    assert to_vnd(1.2, "bVND") == 1_200_000_000
    assert unit_from_name("Doanh thu (Tỷ đồng)", "VND") == "bVND"
    assert unit_from_name("Doanh thu (đồng)", "bVND") == "VND"
    with pytest.raises(ValueError):
        to_vnd(1, "USD")


def test_item_code_maps_vi_and_en():
    assert item_code("Doanh thu thuần") == item_code("Net Revenue") == "revenue"
    assert item_code("TỔNG CỘNG TÀI SẢN (đồng)") == "total_assets"
    assert item_code("Chi phí bán hàng") == "chi_phi_ban_hang"


def test_url_and_title_dedupe_keys():
    a = "https://www.cafef.vn/abc.chn?utm_source=rss&utm_medium=x#top"
    b = "http://cafef.vn/abc.chn/"
    assert canonical_url(a) == canonical_url(b) and url_hash(a) == url_hash(b)
    assert title_hash("[Video] Hòa Phát lãi lớn!") == title_hash("hoà phát   lãi lớn")


# ------------------------------------------------------------------ upsert + ưu tiên nguồn
def _price(close, source):
    return [PriceRec("VNM", "2026-10-02", close, close, close, close, 1000, "kVND")]


def test_upsert_idempotent_and_priority():
    with session_scope() as s:
        pipeline.process_prices(s, _price(60, "demo"), "demo")  # ưu tiên thấp
        pipeline.process_prices(s, _price(61, "vnstock_vci"), "vnstock_vci")  # ưu tiên cao nhất -> ghi đè
        pipeline.process_prices(s, _price(62, "demo"), "demo")  # thấp hơn -> không ghi đè
        pipeline.process_prices(s, _price(61, "vnstock_vci"), "vnstock_vci")  # chạy lại -> không trùng
    with session_scope() as s:
        rows = s.scalars(select(PriceDaily)).all()
        assert len(rows) == 1
        assert rows[0].close == 61_000 and rows[0].source == "vnstock_vci"


def test_upsert_dedupes_within_batch():
    with session_scope() as s:
        n = upsert(s, Symbol, [
            {"ticker": "AAA", "company_name": "x", "source": "demo", "source_priority": 2},
            {"ticker": "AAA", "company_name": "y", "source": "vnstock_vci", "source_priority": 0},
        ], ["ticker"])
        assert n == 1
    with session_scope() as s:
        assert s.get(Symbol, "AAA").company_name == "y"


def test_financials_and_ratios_normalised():
    with session_scope() as s:
        pipeline.process_financials(s, [FinancialRec("FPT", "Q2/2026", "IS", "Doanh thu thuần", 1.5, "bVND")], "demo")
        pipeline.process_ratios(s, [RatioRec("FPT", "2026-Q2", pe=15, pb=3, roe=21.5, eps=5000)], "demo")
    with session_scope() as s:
        f = s.scalars(select(FinancialItem)).one()
        assert (f.period, f.item_code, f.value) == ("2026Q2", "revenue", 1.5e9)
        r = s.scalars(select(Ratio)).one()
        assert r.period == "2026Q2" and r.roe == pytest.approx(0.215)


# ------------------------------------------------------------------ tin tức
def test_news_dedupe_by_url_and_title():
    recs = [
        NewsRec("https://cafef.vn/a.chn?utm_source=rss", "Vinamilk chia cổ tức"),
        NewsRec("https://cafef.vn/a.chn", "Vinamilk chia cổ tức"),  # cùng URL
        NewsRec("https://baokhac.vn/x", "Vinamilk chia cổ tức!"),  # đăng lại, cùng tiêu đề
        NewsRec("https://cafef.vn/b.chn", "FPT ký hợp đồng mới"),
    ]
    with session_scope() as s:
        pipeline.process_news(s, recs, "cafef")
        pipeline.process_news(s, [NewsRec("https://vneconomy.vn/z", "Vinamilk chia cổ tức")], "vneconomy")
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(News)) == 2


def test_tagger():
    tg = Tagger(
        [("HPG", "Công ty Cổ phần Tập đoàn Hòa Phát", "Hòa Phát"), ("VNM", "Công ty Cổ phần Sữa Việt Nam", "Vinamilk"),
         ("HSG", "Công ty Cổ phần Tập đoàn Hoa Sen", None), ("NKG", "Công ty Cổ phần Thép Nam Kim", None),
         ("VND", "Công ty Cổ phần Chứng khoán VNDIRECT", "VNDIRECT"), ("FPT", "Công ty Cổ phần FPT", None)],
        aliases={"VNM": ["Vinamilk"]}, ambiguous={"VND"},
    )
    m = {x.ticker: x for x in tg.tag("Hòa Phát (HPG) báo lãi", "Cổ phiếu HSG và NKG tăng. Vinamilk đi ngang. Giá 50.000 VND (VND).")}
    assert m["HPG"].score == 0.95 and m["VNM"].match_type == "name"
    assert "VND" not in m  # từ viết tắt tiền tệ không bị gắn nhầm
    assert m["HSG"].match_type == "explicit"  # "Cổ phiếu HSG"
    assert "NKG" not in m  # nhắc 1 lần trong thân bài: 0.5 < ngưỡng
    assert [x.ticker for x in tg.tag("VND: Nghị quyết HĐQT")] == ["VND"]
    assert [x.ticker for x in tg.tag("FPT: Báo cáo quản trị", hints=["FPT"])] == ["FPT"]


def test_tag_news_end_to_end():
    with session_scope() as s:
        pipeline.process_symbols(s, [SymbolRec("HPG", "HOSE", "Công ty Cổ phần Tập đoàn Hòa Phát", "Hòa Phát")], "demo")
        pipeline.process_news(s, [NewsRec("https://x.vn/1", "Hòa Phát lãi lớn", content="..."),
                                  NewsRec("https://hnx.vn/2", "SHS: Nghị quyết", kind="disclosure", ticker_hints=["SHS"])], "cafef")
    assert pipeline.tag_news() == 1
    with session_scope() as s:
        tags = s.execute(select(NewsTicker.ticker, NewsTicker.match_type)).all()
        assert ("HPG", "name") in tags and ("SHS", "source") in tags
    assert pipeline.tag_news(all_news=True) == 1  # gắn lại không sinh trùng, giữ mã nguồn gắn sẵn


# ------------------------------------------------------------------ raw -> xử lý lại
def test_reprocess_from_raw_without_recrawl(monkeypatch):
    from finplat import jobs
    from finplat.sources.demo import DemoSource

    calls = {"n": 0}
    orig = DemoSource.fetch

    def counting_fetch(self, dataset, **p):
        calls["n"] += 1
        return orig(self, dataset, **p)

    monkeypatch.setattr(DemoSource, "fetch", counting_fetch)

    def boom(*a, **k):
        raise RuntimeError("lỗi xử lý giả")

    monkeypatch.setitem(pipeline.PROCESSORS, "prices", boom)
    with jobs.job_run("t") as ctx:
        jobs.crawl(ctx, "demo", "prices", today="2026-10-02")
    with session_scope() as s:
        assert s.scalars(select(RawBatch.status)).one() == "failed"
        assert s.scalar(select(func.count()).select_from(PriceDaily)) == 0

    monkeypatch.setitem(pipeline.PROCESSORS, "prices", pipeline.process_prices)
    res = pipeline.process_pending()
    assert res["batches"] == 1 and calls["n"] == 1  # không fetch lại
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(PriceDaily)) > 200
        n1 = s.scalar(select(func.count()).select_from(PriceDaily))
    pipeline.reprocess()  # chạy lại toàn bộ từ raw -> không trùng
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(PriceDaily)) == n1


# ------------------------------------------------------------------ nguồn
def test_rss_source(monkeypatch):
    from finplat.sources import rss

    class Resp:
        def __init__(self, text):
            self.text, self.content = text, text.encode()

    def fake_get(url, timeout=20):
        return Resp((FIX / ("cafef.rss" if url.endswith(".rss") else "article.html")).read_text(encoding="utf-8"))

    monkeypatch.setattr(rss, "http_get", fake_get)
    src = rss.RssSource("cafef", "CafeF", ["https://cafef.vn/thi-truong-chung-khoan.rss"])
    raw = src.fetch("news", skip=lambda u: "gia-usd" in u)
    assert len(raw) == 2 and raw[1].get("_skipped_content")
    recs = src.parse("news", raw)
    assert recs[0].published_at == "2026-10-02T01:30:00"  # đổi sang UTC
    assert "38.000 tỷ đồng" in (recs[0].content or "")
    assert recs[0].summary == "Tập đoàn Hòa Phát vừa công bố BCTC."


def test_disclosure_source():
    from finplat.sources.disclosures import DisclosureSource

    src = DisclosureSource("hnx", {"url": "https://www.hnx.vn/x.html", "item_xpath": "//table//tr[td]//a[@href]",
                                   "base_url": "https://www.hnx.vn"})
    recs = src.parse("disclosures", [{"fetched_html": (FIX / "hnx.html").read_text(encoding="utf-8")}])
    assert len(recs) == 2
    assert recs[0].ticker_hints == ["SHS"] and recs[0].url == "https://www.hnx.vn/vi-vn/tin-cbtt/12345.html"
    assert recs[0].published_at.startswith("2026-10-02T16:45:00+07:00")


def test_vnstock_parse_shapes():
    from finplat.sources.vnstock_source import VnstockSource, _df_records

    src = VnstockSource("VCI")
    # giá: VCI trả nghìn đồng
    prices = src.parse("prices", [{"ticker": "VNM", "time": "2026-10-02 00:00:00", "open": 60.1, "high": 61,
                                    "low": 59.5, "close": 60.5, "volume": 1_200_000},
                                   {"ticker": "XXX", "_error": "ConnectionError"}])
    assert len(prices) == 1 and prices[0].unit == "kVND" and prices[0].date == "2026-10-02"

    # BCTC dạng rộng (lang=vi)
    fin = src.parse("financials", [{"ticker": "VNM", "CP": "VNM", "Năm": 2026, "Kỳ": 2, "_statement": "IS",
                                     "Doanh thu thuần": 1.6e13, "Lợi nhuận sau thuế": 2.5e12, "Ghi chú": "abc"}])
    assert {(f.item_name, f.year, f.quarter) for f in fin} == {("Doanh thu thuần", 2026, 2), ("Lợi nhuận sau thuế", 2026, 2)}

    # chỉ số: cột MultiIndex
    df = pd.DataFrame([["VNM", 2026, 2, 16.2, 4.1, 0.28, 4500]], columns=pd.MultiIndex.from_tuples([
        ("Meta", "CP"), ("Meta", "Năm"), ("Meta", "Kỳ"), ("Chỉ tiêu định giá", "P/E"), ("Chỉ tiêu định giá", "P/B"),
        ("Chỉ tiêu khả năng sinh lợi", "ROE (%)"), ("Chỉ tiêu định giá", "EPS (VND)")]))
    raw = _df_records(df)
    for r in raw:
        r["ticker"] = "VNM"
    (ratio,) = src.parse("ratios", raw)
    assert (ratio.year, ratio.quarter, ratio.pe, ratio.pb, ratio.roe, ratio.eps) == (2026, 2, 16.2, 4.1, 0.28, 4500)

    ev = src.parse("events", [{"ticker": "VNM", "_kind": "dividends", "exercise_date": "2026-07-10",
                                "cash_year": 2025, "cash_dividend_percentage": 0.15, "issue_method": "cash"},
                               {"ticker": "VNM", "_kind": "events", "event_title": "Phát hành thêm cổ phiếu",
                                "public_date": "2026-05-01"}])
    assert ev[0].event_type == "dividend_cash" and ev[0].value == pytest.approx(1500)
    assert ev[1].event_type == "issuance" and ev[1].event_date == "2026-05-01"
