"""Declarative base. String PKs are UUID7 so they sort by creation time, which
is what lets cursor pagination tie-break on id without a second index."""

from __future__ import annotations

import os
import struct
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UtcDateTime(TypeDecorator[datetime]):
    """SQLite drops tzinfo. Re-attach UTC on the way out, so every datetime in
    the app is aware and `iso()` never guesses."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def uuid7() -> str:
    """UUID version 7: 48-bit millisecond timestamp, then randomness."""
    ms = int(time.time() * 1000)
    rand = os.urandom(10)
    raw = struct.pack(">Q", ms)[2:] + rand  # 6 + 10 = 16 bytes
    b = bytearray(raw)
    b[6] = (b[6] & 0x0F) | 0x70  # version 7
    b[8] = (b[8] & 0x3F) | 0x80  # RFC 4122 variant
    return str(uuid.UUID(bytes=bytes(b)))


def prefixed_id(prefix: str) -> str:
    """Readable IDs in logs and URLs: `doc_018f…`, `c_018f…`, `m_018f…`."""
    return f"{prefix}_{uuid7().replace('-', '')[:24]}"


def utcnow() -> datetime:
    return datetime.now(UTC)


def pk(prefix: str) -> Any:
    return mapped_column(primary_key=True, default=lambda: prefixed_id(prefix), nullable=False)


def created_at_col() -> Any:
    return mapped_column(UtcDateTime, default=utcnow, nullable=False, index=True)
