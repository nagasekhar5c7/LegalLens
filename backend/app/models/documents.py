"""Documents, pages, spans, chunks, ingestion jobs.

`document_pages` and `spans` are what make a citation clickable (FR-4, FR-13):
the page's extracted text is encrypted on disk, and the span is a page-local
character range into it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UtcDateTime, created_at_col, pk, utcnow


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = pk("doc")
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    ext: Mapped[str] = mapped_column(String(16), nullable=False)
    mime: Mapped[str] = mapped_column(String(200), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # FR-5: classifier output vs. user override, with the provenance kept.
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="other")
    type_source: Mapped[str] = mapped_column(String(16), nullable=False, default="detected")
    classifier_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # FR-3 lifecycle. FR-7: only `ready` enters retrieval.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    blob_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True, index=True)

    document_pages: Mapped[list[DocumentPage]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentPage.page_no"
    )
    spans: Mapped[list[DocumentSpan]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    chunks: Mapped[list[ChunkMeta]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[IngestionJob]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_documents_library", "user_id", "deleted_at", "uploaded_at"),
        Index("ix_documents_dedupe", "user_id", "sha256", "deleted_at"),
    )


class DocumentPage(Base):
    """One row per page. `text_key` and `image_key` point at encrypted objects
    in the blob store — extracted text is as privileged as the PDF (DR-5)."""

    __tablename__ = "document_pages"

    id: Mapped[str] = pk("pg")
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Document-global offset of this page's first character. Spans are
    # page-local; this is what converts between the two when needed.
    doc_char_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    text_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    image_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    parse_engine: Mapped[str | None] = mapped_column(String(64), nullable=True)

    document: Mapped[Document] = relationship(back_populates="document_pages")

    __table_args__ = (Index("ix_pages_doc_page", "document_id", "page_no", unique=True),)


class DocumentSpan(Base):
    """FR-11. Offsets are page-local — `page` plus `[char_start, char_end)` into
    that page's text. The viewer renders page-at-a-time, so this is the unit it
    can actually highlight."""

    __tablename__ = "spans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String(64), nullable=True)

    document: Mapped[Document] = relationship(back_populates="spans")

    __table_args__ = (Index("ix_spans_doc_page", "document_id", "page_no"),)


class ChunkMeta(Base):
    """chunk_id ↔ vector point. `vector_point_id` is NULL through M3 because no
    vector store exists yet; the column is here so M4 adds a value, not a table."""

    __tablename__ = "chunk_meta"

    id: Mapped[str] = pk("ch")
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_chunk_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    page_from: Mapped[int] = mapped_column(Integer, nullable=False)
    page_to: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    clause_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    index_version: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    vector_point_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    document: Mapped[Document] = relationship(back_populates="chunks")


class IngestionJob(Base):
    """FR-3: one job per file, so a single failure never fails the batch. The
    queue is a table rather than an in-memory list because durable status is the
    actual requirement."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = pk("job")
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # queued | running | done | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    step: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    steps_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Visibility timeout: a job whose lease has expired is reclaimable after a
    # crash, which is what makes restart recovery possible.
    leased_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True, index=True)
    available_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = created_at_col()

    document: Mapped[Document] = relationship(back_populates="jobs")

    __table_args__ = (Index("ix_jobs_claim", "status", "available_at"),)
