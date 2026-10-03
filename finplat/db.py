"""Kết nối DB và upsert theo ưu tiên nguồn (PostgreSQL hoặc SQLite)."""
from __future__ import annotations

import os
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Sequence

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base
from .utils import chunks, utcnow


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # nhiều job chạy song song cần WAL
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    # PostgreSQL: pool đủ cho nhiều luồng crawl + nhiều request API cùng lúc (DB_POOL_SIZE trong .env)
    pool = int(os.environ.get("DB_POOL_SIZE", "10"))
    return create_engine(url, pool_pre_ping=True, pool_size=pool, max_overflow=pool * 2)


def get_sessionmaker(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    session = get_sessionmaker(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(url: str | None = None) -> None:
    Base.metadata.create_all(get_engine(url))


def _insert_for(session: Session):
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover
        raise NotImplementedError(f"Upsert chưa hỗ trợ dialect {dialect}")
    return insert


def upsert(
    session: Session,
    model,
    rows: Sequence[dict],
    key_cols: Sequence[str],
    *,
    respect_priority: bool = True,
    chunk_size: int = 500,
) -> int:
    """INSERT ... ON CONFLICT DO UPDATE.

    - Chạy lại bao nhiêu lần cũng không sinh dòng trùng.
    - Nếu bảng có cột source_priority: chỉ ghi đè khi nguồn mới ưu tiên bằng hoặc hơn nguồn cũ,
      nên thứ tự chạy các nguồn không ảnh hưởng kết quả.
    """
    if not rows:
        return 0
    table = model.__table__
    has_priority = respect_priority and "source_priority" in table.c
    has_updated = "updated_at" in table.c

    # Khử trùng trong cùng lô (Postgres báo lỗi nếu một lệnh chạm cùng khoá 2 lần):
    # giữ dòng của nguồn ưu tiên nhất, hoà thì dòng đến sau thắng.
    best: dict[tuple, dict] = {}
    for row in rows:
        row = dict(row)
        if has_updated:
            row.setdefault("updated_at", utcnow())
        key = tuple(row[k] for k in key_cols)
        cur = best.get(key)
        if cur is None or not has_priority or row.get("source_priority", 99) <= cur.get("source_priority", 99):
            best[key] = row
    deduped = list(best.values())

    insert = _insert_for(session)
    # Nhóm theo tập cột để mỗi câu lệnh có cùng danh sách cột
    groups: dict[tuple, list[dict]] = {}
    for row in deduped:
        groups.setdefault(tuple(sorted(row)), []).append(row)

    for cols, group in groups.items():
        for part in chunks(group, chunk_size):
            stmt = insert(table).values(part)
            update_cols = [c for c in cols if c not in key_cols]
            if not update_cols:
                stmt = stmt.on_conflict_do_nothing(index_elements=list(key_cols))
            else:
                where = None
                if has_priority:
                    where = table.c.source_priority >= stmt.excluded.source_priority
                stmt = stmt.on_conflict_do_update(
                    index_elements=list(key_cols),
                    set_={c: stmt.excluded[c] for c in update_cols},
                    where=where,
                )
            session.execute(stmt)
    return len(deduped)
