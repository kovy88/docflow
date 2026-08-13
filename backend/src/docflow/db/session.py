"""Async engine and session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from docflow.config import DatabaseSettings, get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    connect_args: dict[str, object] = {}
    url = str(settings.url)
    if "asyncpg" in url:
        connect_args["server_settings"] = {
            # A stuck query must not pin a worker slot forever. Enforced by the
            # database rather than by application-side timeouts, which cannot
            # actually stop work already running on the server.
            "statement_timeout": str(settings.statement_timeout_ms),
            "application_name": "docflow",
        }
    return create_async_engine(
        url,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_pre_ping=settings.pool_pre_ping,
        echo=settings.echo,
        connect_args=connect_args,
    )


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().db)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            # Objects stay usable after commit. Without this, every response
            # serialiser triggers a lazy refresh against a closed session.
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope for non-HTTP callers (worker tasks, scripts, seeds)."""
    factory = get_sessionmaker()
    async with factory() as session:
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


def override_engine(engine: AsyncEngine) -> None:
    """Point the module-level engine at a test database."""
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
