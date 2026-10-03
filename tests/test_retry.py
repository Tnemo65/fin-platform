"""Retry (tenacity) cho crawler: chỉ lỗi tạm thời, backoff, Retry-After, hết lượt thì báo lỗi gốc."""
from pathlib import Path

import pytest
import requests
from tenacity import RetryCallState

from finplat.config import get_settings
from finplat.retrying import is_transient, make_wait, with_retry

FIX = Path(__file__).parent / "fixtures"


def _http_error(status: int, headers: dict | None = None) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    resp.headers.update(headers or {})
    return requests.HTTPError(f"{status} error", response=resp)


@pytest.fixture(autouse=True)
def fast_retry():
    get_settings().raw["retry"] = {"attempts": 3, "wait_min": 0.01, "wait_max": 0.05}
    get_settings().raw["general"]["request_delay"] = 0
    get_settings().raw["rate_limits"] = {}


def test_is_transient_classification():
    assert is_transient(requests.ConnectionError("x"))
    assert is_transient(requests.Timeout("x"))
    assert is_transient(_http_error(429)) and is_transient(_http_error(503))
    assert not is_transient(_http_error(404)) and not is_transient(_http_error(401))
    assert not is_transient(ValueError("no data")) and not is_transient(KeyError("col"))
    assert is_transient(RuntimeError("Read timed out"))  # exception kiểu riêng của thư viện (vnstock)
    assert not is_transient(RuntimeError("Chưa cài vnstock"))


def test_wait_uses_retry_after_capped_by_wait_max():
    wait = make_wait(wait_min=1.0, wait_max=5.0)

    def state_for(exc, attempt=1):
        st = RetryCallState(None, None, (), {})
        st.attempt_number = attempt
        st.set_exception((type(exc), exc, None))
        return st

    assert wait(state_for(_http_error(429, {"Retry-After": "2"}))) == 2.0
    assert wait(state_for(_http_error(503, {"Retry-After": "600"}))) == 5.0  # không vượt wait_max
    w1, w2 = wait(state_for(requests.Timeout(), 1)), wait(state_for(requests.Timeout(), 2))
    assert 1.0 <= w1 <= 2.0 and 2.0 <= w2 <= 4.0  # backoff mũ có jitter


def test_with_retry_recovers_then_gives_up():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ConnectionError("reset")
        return "ok"

    assert with_retry(flaky) == "ok" and calls["n"] == 3

    calls["n"] = 0

    def always():
        calls["n"] += 1
        raise requests.Timeout("slow")

    with pytest.raises(requests.Timeout):  # ném lỗi gốc, không phải RetryError
        with_retry(always)
    assert calls["n"] == 3

    calls["n"] = 0

    def not_found():
        calls["n"] += 1
        raise _http_error(404)

    with pytest.raises(requests.HTTPError):
        with_retry(not_found)
    assert calls["n"] == 1  # lỗi không tạm thời: không thử lại


def test_rss_source_retries_article_download(monkeypatch):
    from finplat.sources import rss

    class Resp:
        def __init__(self, text):
            self.text, self.content = text, text.encode()

    hits: dict[str, int] = {}

    def flaky_get(url, timeout=20):
        hits[url] = hits.get(url, 0) + 1
        if url.endswith(".rss"):
            return Resp((FIX / "cafef.rss").read_text(encoding="utf-8"))
        if "gia-usd" in url:
            raise _http_error(404)  # bài đã gỡ: không thử lại
        if hits[url] < 2:
            raise requests.ConnectionError("reset")  # lần đầu lỗi mạng
        return Resp((FIX / "article.html").read_text(encoding="utf-8"))

    monkeypatch.setattr(rss, "http_get", flaky_get)
    src = rss.RssSource("cafef", "CafeF", ["https://cafef.vn/a.rss"])
    raw = src.fetch("news")
    by_link = {r["link"]: r for r in raw}
    assert by_link["https://cafef.vn/hoa-phat-bao-lai-188.chn?utm_source=rss"]["html"]
    assert hits["https://cafef.vn/hoa-phat-bao-lai-188.chn?utm_source=rss"] == 2
    assert "_content_error" in by_link["https://cafef.vn/gia-usd-189.chn"] and hits["https://cafef.vn/gia-usd-189.chn"] == 1


def test_vnstock_per_ticker_retries_transient_errors():
    from finplat.sources.vnstock_source import VnstockSource

    src = VnstockSource("VCI")
    calls: dict[str, int] = {}

    def fetch_one(t):
        calls[t] = calls.get(t, 0) + 1
        if t == "AAA" and calls[t] == 1:
            raise RuntimeError("HTTPSConnectionPool: Read timed out")
        if t == "BAD":
            raise ValueError("no data")
        return [{"close": 10}]

    out = src._per_ticker(["AAA", "BBB", "BAD"], fetch_one)
    assert calls == {"AAA": 2, "BBB": 1, "BAD": 1}  # AAA thử lại 1 lần, BAD không thử lại
    assert [r.get("ticker") for r in out] == ["AAA", "BBB", "BAD"]
    assert "_error" in out[2] and "_error" not in out[0]
