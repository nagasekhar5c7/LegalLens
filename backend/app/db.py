"""Async engine and session. SQLite today, Postgres at M6 by URL alone."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine

    settings = get_settings()
    url = settings.database_url
    kwargs: dict[str, Any] = {"echo": False, "future": True}
    if _is_sqlite(url):
        # WAL + a real busy timeout is what makes concurrent reads during an
        # ingestion write actually work (NFR-5).
        kwargs["connect_args"] = {"timeout": 30}
    else:
        kwargs["pool_pre_ping"] = True

    _engine = create_async_engine(url, **kwargs)

    if _is_sqlite(url):

        @event.listens_for(_engine.sync_engine, "connect")
        def _pragmas(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    _sessionmaker = async_sessionmaker(
        _engine, expire_on_commit=False, autoflush=False, class_=AsyncSession
    )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def session_scope() -> AsyncIterator[AsyncSession]:
    """Standalone session for workers and scripts — commits on clean exit."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
