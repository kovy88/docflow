"""SQLAlchemy declarative base and shared column conventions."""

from __future__ import annotations

import datetime as dt
import os
import secrets
import time
import uuid

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit constraint naming. Without this, Alembic autogenerate emits anonymous
# constraint names that differ between Postgres versions, which makes downgrade
# migrations unrunnable and diffs unreadable.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def new_id() -> uuid.UUID:
    """Generate a UUIDv7 (time-ordered).

    Random UUIDv4 primary keys scatter inserts across the whole B-tree, which turns
    every insert into a random page write and bloats the index. UUIDv7 puts a
    millisecond timestamp in the high bits, so inserts append to the right-hand edge
    of the index like a sequence would, while keeping the non-enumerable property
    that matters for public identifiers.

    Python 3.11 has no `uuid.uuid7`, so this implements RFC 9562 §5.7 directly:
      48 bits  unix_ts_ms
       4 bits  version (7)
      12 bits  rand_a
       2 bits  variant (0b10)
      62 bits  rand_b
    """
    ts_ms = int(time.time() * 1000) & 0xFFFF_FFFF_FFFF
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)

    value = ts_ms << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class TimestampMixin:
    """`created_at` / `updated_at` maintained by the database, not the application.

    Server-side defaults mean a row written by a migration, a psql session or a
    future service still gets correct timestamps.
    """

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite")


TESTING = os.getenv("DOCFLOW_ENVIRONMENT") == "test"
