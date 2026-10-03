"""Đa luồng ở crawler: song song thật, giữ thứ tự, và vẫn giãn cách request tới nguồn."""
import threading
import time

from finplat import jobs
from finplat.sources import base
from finplat.sources.base import Source
from finplat.utils import RateLimiter, thread_map


class Concurrency:
    """Đếm số luồng đang chạy đồng thời tối đa."""

    def __init__(self):
        self.lock, self.cur, self.peak = threading.Lock(), 0, 0

    def __enter__(self):
        with self.lock:
            self.cur += 1
            self.peak = max(self.peak, self.cur)

    def __exit__(self, *a):
        with self.lock:
            self.cur -= 1


def test_thread_map_parallel_and_ordered():
    c = Concurrency()

    def slow(x):
        with c:
            time.sleep(0.2)
        return x * 2

    t0 = time.monotonic()
    assert thread_map(slow, list(range(8)), workers=4) == [x * 2 for x in range(8)]
    assert time.monotonic() - t0 < 0.8  # 8 x 0.2s tuần tự = 1.6s; 4 luồng ~0.4s
    assert c.peak >= 2
    assert thread_map(slow, [1], workers=4) == [2] and thread_map(slow, [], workers=4) == []


def test_rate_limiter_spaces_requests_across_threads():
    lim = RateLimiter(0.05)
    stamps = []
    lock = threading.Lock()

    def hit(_):
        lim.wait()
        with lock:
            stamps.append(time.monotonic())

    thread_map(hit, range(6), workers=6)
    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert min(gaps) >= 0.045  # không có 2 request nào sát nhau hơn min_interval


def test_crawl_many_fetches_sources_in_parallel_and_stores_in_order(monkeypatch):
    c = Concurrency()
    order = []

    class Slow(Source):
        datasets = ("news",)

        def __init__(self, name):
            self.name = name

        def fetch(self, dataset, **params):
            with c:
                time.sleep(0.3)
            return [{"link": f"https://x.vn/{self.name}", "title": f"Tin {self.name}"}]

        def parse(self, dataset, raw, params=None):
            order.append(self.name)
            return []

    names = ["s1", "s2", "s3"]
    for n in names:
        monkeypatch.setitem(base._REGISTRY, n, Slow(n))
    t0 = time.monotonic()
    with jobs.job_run("t") as ctx:
        jobs.crawl_many(ctx, [(n, "news", {}) for n in names])
    assert time.monotonic() - t0 < 0.8  # 3 x 0.3s tuần tự = 0.9s
    assert c.peak >= 2
    assert order == names  # xử lý tuần tự đúng thứ tự nguồn
    assert len(ctx.notes) == 3 and not ctx.errors


def test_rss_articles_downloaded_in_parallel(monkeypatch):
    from pathlib import Path

    from finplat.sources import rss

    fix = Path(__file__).parent / "fixtures"
    c = Concurrency()
    feed_xml = (fix / "cafef.rss").read_text(encoding="utf-8")
    # feed có 6 bài
    items = feed_xml.split("<item>")
    many = items[0] + "".join("<item>" + items[1].replace("188.chn", f"{i}.chn") for i in range(6)) + "<item>" + items[2]

    class Resp:
        def __init__(self, text):
            self.text, self.content = text, text.encode()

    def fake_get(url, timeout=20):
        if url.endswith(".rss"):
            return Resp(many)
        with c:
            time.sleep(0.2)
        return Resp((fix / "article.html").read_text(encoding="utf-8"))

    monkeypatch.setattr(rss, "http_get", fake_get)
    from finplat.config import get_settings

    get_settings().raw["general"]["request_delay"] = 0  # đo riêng phần song song, không tính giãn cách
    get_settings().raw["rate_limits"] = {}
    src = rss.RssSource("cafef", "CafeF", ["https://cafef.vn/a.rss"])
    t0 = time.monotonic()
    raw = src.fetch("news")
    assert len(raw) == 7 and all(r.get("html") for r in raw)
    assert time.monotonic() - t0 < 1.0  # 7 x 0.2s tuần tự = 1.4s
    assert c.peak >= 2
