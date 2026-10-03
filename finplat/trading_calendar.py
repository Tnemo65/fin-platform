from __future__ import annotations

from datetime import date, datetime, timedelta

from .config import get_settings


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in get_settings().holidays


def last_trading_day(d: date) -> date:
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def today_vn() -> date:
    return datetime.now(get_settings().tz).date()


def in_financial_season(d: date) -> bool:
    for m1, d1, m2, d2 in get_settings().financial_season.get("windows", []):
        if date(d.year, m1, d1) <= d <= date(d.year, m2, d2):
            return True
    return False
