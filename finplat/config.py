"""Cấu hình: biến môi trường (.env) + file config/settings.toml."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Đọc .env đơn giản (KEY=VALUE), không ghi đè biến đã có."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    database_url: str
    raw_dir: Path
    report_dir: Path
    settings_file: Path
    api_url: str
    notify_webhook_url: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    raw: dict = field(default_factory=dict)

    # ---- tiện ích đọc settings.toml ----
    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.raw.get("general", {}).get("timezone", "Asia/Ho_Chi_Minh"))

    def general(self, key: str, default=None):
        return self.raw.get("general", {}).get(key, default)

    def priority_rank(self, dataset: str, source: str) -> int:
        """Số nhỏ hơn = ưu tiên cao hơn. Nguồn không khai báo xếp cuối (99)."""
        order = self.raw.get("priority", {}).get(dataset, [])
        return order.index(source) if source in order else 99

    def enabled_sources(self, job: str) -> list[str]:
        return list(self.raw.get("sources", {}).get(job, []))

    def unit(self, source: str, kind: str, default: str = "VND") -> str:
        return self.raw.get("units", {}).get(source, {}).get(kind, default)

    @property
    def feeds(self) -> dict:
        return self.raw.get("feeds", {})

    @property
    def disclosures(self) -> dict:
        return self.raw.get("disclosures", {})

    @property
    def holidays(self) -> set[str]:
        return set(self.raw.get("calendar", {}).get("holidays", []))

    @property
    def tagging(self) -> dict:
        return self.raw.get("tagging", {})

    @property
    def financial_season(self) -> dict:
        return self.raw.get("financial_season", {})


@lru_cache
def get_settings() -> Settings:
    _load_dotenv(ROOT / ".env")
    settings_file = Path(os.environ.get("SETTINGS_FILE", ROOT / "config" / "settings.toml"))
    with open(settings_file, "rb") as f:
        raw = tomllib.load(f)
    data_dir = Path(os.environ.get("DATA_DIR", ROOT / "data"))
    return Settings(
        database_url=os.environ.get("DATABASE_URL", f"sqlite:///{data_dir / 'finplat.db'}"),
        raw_dir=Path(os.environ.get("RAW_DIR", data_dir / "raw")),
        report_dir=Path(os.environ.get("REPORT_DIR", data_dir / "reports")),
        settings_file=settings_file,
        api_url=os.environ.get("API_URL", "http://localhost:8000"),
        notify_webhook_url=os.environ.get("NOTIFY_WEBHOOK_URL") or None,
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID") or None,
        raw=raw,
    )
