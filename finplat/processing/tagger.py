"""Gắn mã cổ phiếu cho tin: dò ticker và tên công ty trong tiêu đề + nội dung.

Điểm theo độ tin cậy (lấy cao nhất cho mỗi mã), giữ mã có điểm >= min_score:
- explicit 1.0/0.95: mã nguồn gắn sẵn, "VNM: ..." đầu tiêu đề, "(HOSE: VNM)", "mã VNM", "Vinamilk (VNM)"
- name 0.9/0.7: khớp tên công ty/alias trong tiêu đề/nội dung
- ticker 0.8 ở tiêu đề; nội dung 0.5 + 0.1 mỗi lần nhắc thêm (tối đa 0.8)
Mã trùng từ viết tắt phổ biến (VND, CEO, ...) chỉ được gắn qua explicit hoặc name.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..utils import strip_accents

TICKER = r"([A-Z][A-Z0-9]{2})"
EXPLICIT_PATTERNS = [
    re.compile(r"^\s*" + TICKER + r"\s*[:\-–]"),  # "VNM: Nghị quyết..."
    re.compile(r"\((?:HOSE|HSX|HNX|UPCOM|UPCoM)\s*[:\-]\s*" + TICKER + r"\)"),
    re.compile(r"(?i:\bmã(?: chứng khoán| cổ phiếu)?|\bcổ phiếu|\bMCK|\bmã CK)\s*:?\s*" + TICKER + r"\b"),
    re.compile(r"\(\s*" + TICKER + r"\s*\)"),
]
BARE_TICKER = re.compile(r"(?<!\w)" + TICKER + r"(?!\w)")

COMPANY_PREFIXES = [
    "cong ty co phan", "cong ty cp", "ctcp", "tong cong ty", "tap doan", "cong ty tnhh",
    "ngan hang thuong mai co phan", "ngan hang tmcp", "cong ty", "ngan hang",
]
GENERIC_NAMES = {
    "viet nam", "sai gon", "ha noi", "dau tu", "xay dung", "phat trien", "thuong mai", "dich vu",
    "chung khoan", "bat dong san", "san xuat", "xuat nhap khau", "dau tu va phat trien", "cong nghiep",
}


def _norm(text: str) -> str:
    return " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", strip_accents(text).lower())).strip() + " "


def _name_variants(company_name: str | None, short_name: str | None) -> set[str]:
    out = set()
    for name in (company_name, short_name):
        if not name:
            continue
        n = _norm(name).strip()
        for p in COMPANY_PREFIXES:
            if n.startswith(p + " "):
                n = n[len(p) + 1 :]
                break
        if len(n) >= 6 and " " in n and n not in GENERIC_NAMES:
            out.add(n)
    return out


@dataclass
class Match:
    ticker: str
    match_type: str
    score: float


class Tagger:
    def __init__(self, symbols: list[tuple[str, str | None, str | None]], aliases: dict[str, list[str]] | None = None,
                 ambiguous: set[str] | None = None, min_score: float = 0.6):
        self.tickers = {t.upper() for t, *_ in symbols}
        self.ambiguous = {a.upper() for a in (ambiguous or set())}
        self.min_score = min_score
        self.names: list[tuple[str, str]] = []  # (tên chuẩn hoá có khoảng trắng 2 đầu, mã)
        for ticker, company, short in symbols:
            for n in _name_variants(company, short):
                self.names.append((f" {n} ", ticker.upper()))
        for ticker, names in (aliases or {}).items():
            if ticker.upper() not in self.tickers:
                continue
            for n in names:
                self.names.append((_norm(n), ticker.upper()))
        # tên dài khớp trước, tránh tên ngắn nằm trong tên dài
        self.names.sort(key=lambda x: -len(x[0]))

    def tag(self, title: str, content: str | None = None, hints: list[str] | None = None) -> list[Match]:
        best: dict[str, Match] = {}

        def add(ticker: str, kind: str, score: float):
            ticker = ticker.upper()
            if ticker not in self.tickers:
                return
            if ticker in self.ambiguous and kind == "ticker":
                return
            cur = best.get(ticker)
            if cur is None or score > cur.score:
                best[ticker] = Match(ticker, kind, round(score, 2))

        for h in hints or []:
            add(h, "explicit", 1.0)

        body = content or ""
        for text, is_title in ((title, True), (body, False)):
            for i, pat in enumerate(EXPLICIT_PATTERNS):
                if i == 0 and not is_title:
                    continue
                for m in pat.finditer(text):
                    if i == 3 and m.group(1) in self.ambiguous:  # "(VND)" thường là đơn vị tiền
                        continue
                    add(m.group(1), "explicit", 1.0 if i < 3 else 0.95)

        for m in BARE_TICKER.finditer(title):
            add(m.group(1), "ticker", 0.8)
        counts: dict[str, int] = {}
        for m in BARE_TICKER.finditer(body):
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
        for t, c in counts.items():
            add(t, "ticker", min(0.8, 0.5 + 0.1 * (c - 1)))

        ntitle, nbody = _norm(title), _norm(body)
        for name, ticker in self.names:
            if name in ntitle:
                add(ticker, "name", 0.9)
            elif name in nbody:
                add(ticker, "name", 0.7)

        return sorted((m for m in best.values() if m.score >= self.min_score), key=lambda m: -m.score)
