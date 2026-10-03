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
    yield tmp_path
    db.get_engine().dispose()
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
