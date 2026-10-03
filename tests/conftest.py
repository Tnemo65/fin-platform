import os

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Mỗi test một DB sạch + thư mục raw riêng.

    Mặc định SQLite. Đặt TEST_DATABASE_URL=postgresql+psycopg://... để chạy trên PostgreSQL.
    """
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", url)
    from finplat import config, db
    from finplat.models import Base

    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    Base.metadata.drop_all(db.get_engine())
    db.init_db()
    # Test không được gọi mạng thật: retry nhanh, không giãn cách
    raw = config.get_settings().raw
    raw["retry"] = {"attempts": 2, "wait_min": 0.01, "wait_max": 0.02}
    raw["rate_limits"] = {}
    raw["general"]["request_delay"] = 0
    yield tmp_path
    db.get_engine().dispose()
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()


@pytest.fixture
def offline_sources(monkeypatch):
    """Mọi nguồn gọi mạng (vnstock, VNDirect, CafeF) đều báo lỗi kết nối ngay."""
    from finplat.sources.httpbase import HttpTickerSource
    from finplat.sources.vnstock_source import VnstockSource

    def broken(self, dataset, **params):
        raise ConnectionError("không kết nối được")

    monkeypatch.setattr(VnstockSource, "fetch", broken)
    monkeypatch.setattr(HttpTickerSource, "fetch", broken)
