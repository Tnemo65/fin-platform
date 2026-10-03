"""Chuẩn hoá: đơn vị về VND, kỳ về dạng 2026Q2, ngày giờ về UTC, tên chỉ tiêu về mã chung."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..utils import sha1, slugify, strip_accents

UNIT_MULTIPLIER = {
    "VND": 1,
    "kVND": 1_000,  # nghìn đồng
    "mVND": 1_000_000,  # triệu đồng
    "bVND": 1_000_000_000,  # tỷ đồng
}

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}


def to_vnd(value: float | None, unit: str | None) -> float | None:
    if value is None:
        return None
    unit = unit or "VND"
    if unit not in UNIT_MULTIPLIER:
        raise ValueError(f"Đơn vị không hỗ trợ: {unit}")
    return float(value) * UNIT_MULTIPLIER[unit]


def normalize_period(period: str | None = None, year: int | None = None, quarter: int | None = None) -> tuple[str, int, int]:
    """Trả về (kỳ chuẩn, năm, quý). Quý 0 = cả năm, kỳ dạng 2026Y.

    Nhận: "2026Q2", "2026-Q2", "Q2/2026", "Q2 2026", "Quý 2/2026", "Quý II năm 2026", "2026",
    hoặc year/quarter rời (quarter=5 ở một số API nghĩa là cả năm).
    """
    if year is None and period:
        p = strip_accents(str(period)).upper().replace("QUY", "Q").replace("NAM", " ")
        p = re.sub(r"\s+", " ", p).strip()
        m = re.search(r"(\d{4})\s*[-/ ]?\s*Q\s*([1-4]|IV|III|II|I)\b", p) or None
        if m:
            year, q = int(m.group(1)), m.group(2)
        else:
            m = re.search(r"Q\s*([1-4]|IV|III|II|I)\s*[-/ ]?\s*(\d{4})", p)
            if m:
                q, year = m.group(1), int(m.group(2))
            else:
                m = re.fullmatch(r"(?:FY|Y)?\s*(\d{4})\s*(?:FY|Y)?", p)
                if not m:
                    raise ValueError(f"Không hiểu kỳ báo cáo: {period!r}")
                year, q = int(m.group(1)), "0"
        quarter = ROMAN.get(q, None) or int(q)
    if year is None:
        raise ValueError("Thiếu năm của kỳ báo cáo")
    quarter = int(quarter or 0)
    if quarter == 5:
        quarter = 0
    if quarter not in (0, 1, 2, 3, 4):
        raise ValueError(f"Quý không hợp lệ: {quarter}")
    return (f"{year}Y" if quarter == 0 else f"{year}Q{quarter}"), int(year), quarter


def prev_year_period(period: str) -> str:
    _, y, q = normalize_period(period)
    return f"{y - 1}Y" if q == 0 else f"{y - 1}Q{q}"


def parse_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Không hiểu ngày: {value!r}")


def parse_datetime_utc(value) -> datetime | None:
    """Có múi giờ thì đổi sang UTC; không có thì coi như đã là UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            from email.utils import parsedate_to_datetime

            try:
                dt = parsedate_to_datetime(s)
            except (TypeError, ValueError):
                return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ----------------------------------------------------------------- tin tức
TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|zarsrc|zaloid|_ga)", re.I)


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/") or "/"
    host = parts.netloc.lower().removeprefix("www.").removeprefix("m.")
    return urlunsplit(("https", host, path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return sha1(canonical_url(url))


def normalize_title(title: str) -> str:
    t = re.sub(r"^\[[^\]]*\]\s*", "", title.strip())  # bỏ nhãn kiểu [Video]
    t = strip_accents(t).lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def title_hash(title: str) -> str:
    return sha1(normalize_title(title))


# ----------------------------------------------------------------- BCTC
# Mã chuẩn cho các chỉ tiêu chính, để ghép được giữa các nguồn. Chỉ tiêu khác dùng slug tên.
CANONICAL_ITEMS: dict[str, list[str]] = {
    "revenue": ["doanh thu thuan", "doanh thu thuan ve ban hang va cung cap dich vu", "net revenue", "revenue",
                "doanh thu (dong)", "doanh thu"],
    "gross_profit": ["loi nhuan gop", "lai gop", "gross profit", "loi nhuan gop ve ban hang va cung cap dich vu"],
    "operating_profit": ["loi nhuan thuan tu hoat dong kinh doanh", "operating profit/loss", "operating profit"],
    "profit_before_tax": ["loi nhuan truoc thue", "tong loi nhuan ke toan truoc thue", "profit before tax",
                          "lai/(lo) truoc thue"],
    "net_profit": ["loi nhuan sau thue", "loi nhuan sau thue thu nhap doanh nghiep", "net profit for the year",
                   "net profit", "loi nhuan thuan", "lai/(lo) thuan sau thue", "post tax profit"],
    "net_profit_parent": ["loi nhuan sau thue cua co dong cong ty me", "co dong cua cong ty me",
                          "attributable to parent company", "net profit attributable to parent company",
                          "co dong cua cong ty me (dong)"],
    "total_assets": ["tong cong tai san", "tong tai san", "total assets", "tong cong tai san (dong)"],
    "total_liabilities": ["no phai tra", "tong no phai tra", "liabilities", "total liabilities", "no phai tra (dong)"],
    "owners_equity": ["von chu so huu", "owner's equity", "owners' equity", "von chu so huu (dong)"],
    "cash": ["tien va tuong duong tien", "cash and cash equivalents"],
    "cfo": ["luu chuyen tien thuan tu hoat dong kinh doanh", "net cash flows from operating activities",
            "luu chuyen tien te rong tu cac hoat dong sxkd"],
    "cfi": ["luu chuyen tien thuan tu hoat dong dau tu", "net cash flows from investing activities",
            "luu chuyen tu hoat dong dau tu"],
    "cff": ["luu chuyen tien thuan tu hoat dong tai chinh", "net cash flows from financing activities",
            "luu chuyen tien tu hoat dong tai chinh"],
}
_ALIAS_TO_CODE = {alias: code for code, aliases in CANONICAL_ITEMS.items() for alias in aliases}
KEY_ITEMS = list(CANONICAL_ITEMS)


def item_code(name: str) -> str:
    norm = re.sub(r"\s+", " ", strip_accents(name).lower()).strip()
    norm = re.sub(r"^[ivx0-9]+[\.\)]\s*", "", norm)  # bỏ số mục "I. ", "1. "
    if norm in _ALIAS_TO_CODE:
        return _ALIAS_TO_CODE[norm]
    stripped = re.sub(r"\s*\((dong|vnd|ty dong|trieu dong)\)$", "", norm)
    return _ALIAS_TO_CODE.get(stripped) or slugify(stripped)


_UNIT_IN_NAME = [
    (re.compile(r"\((ty|ti) (dong|vnd)\)"), "bVND"),
    (re.compile(r"\(trieu (dong|vnd)\)"), "mVND"),
    (re.compile(r"\(nghin (dong|vnd)\)"), "kVND"),
    (re.compile(r"\((dong|vnd)\)"), "VND"),
    (re.compile(r"\(bn vnd\)|\(billion vnd\)"), "bVND"),
]


def unit_from_name(name: str, default: str) -> str:
    """Một số nguồn ghi đơn vị trong tên cột, vd "Doanh thu (Tỷ đồng)": ưu tiên đơn vị đó."""
    norm = strip_accents(name).lower()
    for pattern, unit in _UNIT_IN_NAME:
        if pattern.search(norm):
            return unit
    return default
