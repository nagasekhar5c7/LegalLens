"""Audit, usage, traces, version registries — the accountability tables.

DR-4 requires an audit row for every retrieval and generation. It is written in
the same transaction as the message it describes: if the audit write could fail
independently, the audit would be fiction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, pk


class AuditLog(Base):
    """DR-4. Retained past a conversation's soft delete and past a document's
    hard delete — a tombstone is the only thing hard delete leaves behind."""

    __tablename__ = "audit_log"

    id: Mapped[str] = pk("au")
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    message_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    document_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    span_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    index_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # A hash, not the text. DR-5: no document content in telemetry, and the
    # audit log is telemetry the moment it is exported.
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (Index("ix_audit_user_time", "user_id", "created_at"),)


class UsageTransaction(Base):
    """NFR-8: cost per request, attributed and visible. A ledger row, not a log
    line, because `GET /api/usage` has to add them up."""

    __tablename__ = "usage_transactions"

    id: Mapped[str] = pk("ux")
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    message_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    document_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # turn | ingestion
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="turn", index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ttft_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = created_at_col()


class Trace(Base):
    """NFR-11: retrieval → assembly → generation, with latency per stage, and
    the candidate set with scores so a retrieval failure is diagnosable without
    re-running the query."""

    __tablename__ = "traces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=True, index=True
    )
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    stages_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    queries_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    candidates_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    timings_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    versions_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = created_at_col()


class PromptVersion(Base):
    """NFR-12: stamped on every generation."""

    __tablename__ = "prompt_versions"

    id: Mapped[str] = pk("pv")
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (Index("ix_prompt_name_ver", "name", "version", unique=True),)


class IndexVersion(Base):
    """NFR-12 / M6: an embedding-model or chunking change becomes a new index
    version, which is what makes the rollback story real."""

    __tablename__ = "index_versions"

    id: Mapped[str] = pk("iv")
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chunker: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reranker: Mapped[str | None] = mapped_column(String(128), nullable=True)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = created_at_col()
