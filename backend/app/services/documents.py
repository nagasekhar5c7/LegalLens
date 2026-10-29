"""Document reads, upload persistence, type override and hard delete.

The hard-delete path is the one worth reading closely. FR-26 requires file,
extracted text, chunks and embeddings to go, effective immediately for
retrieval, and TS-6 says that is verified by test rather than asserted. So
`hard_delete` enumerates what it removed and returns the counts, which is both
what the audit row records and what `tests/test_hard_delete.py` asserts on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import UnaryExpression

from app.config import get_settings
from app.models import ChunkMeta, Document, DocumentPage, DocumentSpan, IngestionJob
from app.models.base import utcnow
from app.schemas.base import iso
from app.schemas.documents import (
    DocumentFailure,
    DocumentStatus,
    IngestionStep,
    LegalDocument,
    OcrInfo,
    WorkspaceHealth,
)
from app.storage import blob

log = logging.getLogger("legallens.documents")


def _rowcount(result: Any) -> int:
    """See `app.ingestion.queue._rowcount` — same reason."""
    return int(getattr(result, "rowcount", 0) or 0)


#: Statuses that still count as in-flight for the workspace health card.
IN_FLIGHT = ("queued", "parsing", "ocr", "indexing")


def to_schema(document: Document, *, jobs: list[IngestionJob] | None = None) -> LegalDocument:
    """`jobs` is passed explicitly rather than read off the relationship.

    Under async SQLAlchemy a lazy load raises MissingGreenlet instead of quietly
    issuing a query, so every serialiser that needs a relationship has to say so
    at the call site. Callers that eager-loaded pass `document.jobs`; the upload
    path passes the job it just created.
    """
    return LegalDocument(
        id=document.id,
        name=document.name,
        type=document.type,
        type_source=document.type_source,
        classifier_confidence=document.classifier_confidence,
        pages=document.pages,
        size_bytes=document.size_bytes,
        status=document.status,
        progress=document.progress,
        uploaded_at=iso(document.uploaded_at) or "",
        ocr=OcrInfo.model_validate(document.ocr_json) if document.ocr_json else None,
        failure=(
            DocumentFailure(
                reason=document.failure_reason or "",
                detail=document.failure_detail or "",
            )
            if document.failure_reason
            else None
        ),
        ingestion=_steps(jobs),
    )


def to_status(document: Document, *, jobs: list[IngestionJob] | None = None) -> DocumentStatus:
    return DocumentStatus(
        id=document.id,
        status=document.status,
        progress=document.progress,
        pages=document.pages,
        ingestion=_steps(jobs) or [],
        failure=(
            DocumentFailure(
                reason=document.failure_reason or "", detail=document.failure_detail or ""
            )
            if document.failure_reason
            else None
        ),
        ocr=OcrInfo.model_validate(document.ocr_json) if document.ocr_json else None,
    )


def _steps(jobs: list[IngestionJob] | None) -> list[IngestionStep] | None:
    """The ingestion log comes from the newest job, so a retry shows the
    retry's progress rather than the original attempt's."""
    if not jobs:
        return None
    newest = max(jobs, key=lambda j: j.created_at)
    if not newest.steps_json:
        return None
    return [IngestionStep.model_validate(s) for s in newest.steps_json]


async def list_documents(
    db: AsyncSession,
    user_id: str,
    *,
    doc_type: str | None = None,
    status: str | None = None,
    sort: str = "uploaded_desc",
) -> list[Document]:
    from sqlalchemy.orm import selectinload

    stmt = (
        select(Document)
        .where(Document.user_id == user_id, Document.deleted_at.is_(None))
        .options(selectinload(Document.jobs))
    )
    if doc_type:
        stmt = stmt.where(Document.type == doc_type)
    if status:
        stmt = stmt.where(Document.status == status)

    orders: dict[str, UnaryExpression[Any]] = {
        "uploaded_desc": Document.uploaded_at.desc(),
        "uploaded_asc": Document.uploaded_at.asc(),
        "name_asc": Document.name.asc(),
        "name_desc": Document.name.desc(),
        "pages_desc": Document.pages.desc(),
        "pages_asc": Document.pages.asc(),
    }
    # An unrecognised `sort` falls back rather than erroring: the library view
    # is a read, and a bad query string should not break it.
    order = orders.get(sort, orders["uploaded_desc"])

    return list((await db.execute(stmt.order_by(order))).scalars())


async def get_document(db: AsyncSession, user_id: str, document_id: str) -> Document | None:
    from sqlalchemy.orm import selectinload

    stmt = (
        select(Document)
        .where(
            Document.id == document_id,
            Document.user_id == user_id,
            Document.deleted_at.is_(None),
        )
        .options(selectinload(Document.jobs))
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def find_duplicate(db: AsyncSession, user_id: str, sha256: str) -> Document | None:
    """FR-1 dedupe by content hash. Re-uploading the same executed agreement is
    a normal thing to do by accident, and a second copy would show up twice in
    every scope picker."""
    stmt = select(Document).where(
        Document.user_id == user_id,
        Document.sha256 == sha256,
        Document.deleted_at.is_(None),
    )
    return (await db.execute(stmt)).scalars().first()


async def count_documents(db: AsyncSession, user_id: str) -> int:
    result = await db.execute(
        select(func.count(Document.id)).where(
            Document.user_id == user_id, Document.deleted_at.is_(None)
        )
    )
    return int(result.scalar_one())


async def workspace_health(db: AsyncSession, user_id: str) -> WorkspaceHealth:
    rows = list(
        (
            await db.execute(
                select(Document.status, Document.pages).where(
                    Document.user_id == user_id, Document.deleted_at.is_(None)
                )
            )
        ).all()
    )
    return WorkspaceHealth(
        documents=len(rows),
        ready=sum(1 for s, _ in rows if s == "ready"),
        indexing=sum(1 for s, _ in rows if s in IN_FLIGHT),
        failed=sum(1 for s, _ in rows if s == "failed"),
        pages=sum(p for s, p in rows if s == "ready"),
    )


async def set_type(db: AsyncSession, document: Document, doc_type: str) -> None:
    """FR-5: the override is recorded as an override. The badge has to be able
    to say "overridden" rather than claiming the classifier got it right."""
    document.type = doc_type
    document.type_source = "overridden"


# --------------------------------------------------------------- hard delete


@dataclass
class DeletionReport:
    """What was actually removed. FR-26 is verified by test (TS-6), and this is
    the object the test reads."""

    document_id: str
    chunks: int = 0
    spans: int = 0
    pages: int = 0
    blobs: int = 0
    vector_points: int = 0
    jobs: int = 0
    errors: list[str] = field(default_factory=list)

    def as_detail(self) -> dict[str, object]:
        return {
            "chunks": self.chunks,
            "spans": self.spans,
            "pages": self.pages,
            "blobs": self.blobs,
            "vector_points": self.vector_points,
            "jobs": self.jobs,
            "errors": self.errors,
        }


async def hard_delete(db: AsyncSession, document: Document) -> DeletionReport:
    """FR-26 / TS-6. Order matters: dependents first, blobs after the rows that
    reference them, the document row last.

    The row deletes commit with the caller's transaction. The blob unlinks are
    not transactional, so they run after the rows are gone — an orphaned file is
    recoverable by the startup sweep, whereas a row pointing at a deleted file
    would make the viewer throw.
    """
    report = DeletionReport(document_id=document.id)

    page_keys: list[str] = []
    pages = list(
        (
            await db.execute(select(DocumentPage).where(DocumentPage.document_id == document.id))
        ).scalars()
    )
    for page in pages:
        if page.text_key:
            page_keys.append(page.text_key)
        if page.image_key:
            page_keys.append(page.image_key)

    report.chunks = _rowcount(
        await db.execute(sql_delete(ChunkMeta).where(ChunkMeta.document_id == document.id))
    )
    report.spans = _rowcount(
        await db.execute(sql_delete(DocumentSpan).where(DocumentSpan.document_id == document.id))
    )
    report.pages = _rowcount(
        await db.execute(sql_delete(DocumentPage).where(DocumentPage.document_id == document.id))
    )
    report.jobs = _rowcount(
        await db.execute(sql_delete(IngestionJob).where(IngestionJob.document_id == document.id))
    )

    # FR-26: the vector points go with everything else. A filtered delete when
    # a vector store is configured, 0 when it is not — and `app/indexing.py`
    # decides which, so this call site does not change either way.
    #
    # Imported here rather than at module scope because resolving the indexer
    # can open the vector store, and a module that is imported by every document
    # route should not do that as a side effect of being imported.
    from app.indexing import get_indexer

    report.vector_points = get_indexer().delete_document(document.id)

    blob_key = document.blob_key
    document_id = document.id

    # Bulk delete rather than `db.delete(document)`: the ORM would cascade into
    # the same child tables the explicit deletes above already cleared, which
    # SQLAlchemy reports as "expected to delete 1 row, 0 matched". Expunge
    # first so the session does not try to manage a row that no longer exists.
    db.expunge(document)
    await db.execute(sql_delete(Document).where(Document.id == document_id))
    await db.flush()

    for key in [*page_keys, *([blob_key] if blob_key else [])]:
        try:
            if blob.delete(key):
                report.blobs += 1
        except OSError as exc:
            report.errors.append(f"{key}: {type(exc).__name__}")

    # Sweep the whole prefix too, so a page image written without a recorded
    # key still goes. Reporting what the enumerated deletes missed is the
    # difference between "delete is real" and "delete is probably real".
    for prefix in (f"pages/{document_id}", f"images/{document_id}", f"files/{document_id}"):
        report.blobs += blob.delete_prefix(prefix)

    log.info("hard-deleted %s: %s", document_id, report.as_detail())
    return report


async def known_blob_prefixes(db: AsyncSession) -> set[str]:
    """For the orphan sweep: every prefix the database still expects to exist."""
    ids = list((await db.execute(select(Document.id))).scalars())
    prefixes: set[str] = set()
    for document_id in ids:
        prefixes.update({f"pages/{document_id}", f"images/{document_id}", f"files/{document_id}"})
    return prefixes


async def soft_delete_stale_work(db: AsyncSession, user_id: str) -> int:
    """Documents stuck in-flight past the visibility timeout with no live job.
    Without this a crashed ingestion leaves a row that is neither usable nor
    retryable from the UI."""
    settings = get_settings()
    cutoff = utcnow().timestamp() - settings.ingest_visibility_timeout_s * 2
    stuck = list(
        (
            await db.execute(
                select(Document).where(
                    Document.user_id == user_id,
                    Document.status.in_(IN_FLIGHT),
                    Document.deleted_at.is_(None),
                )
            )
        ).scalars()
    )
    marked = 0
    for document in stuck:
        if document.uploaded_at.timestamp() > cutoff:
            continue
        live = (
            await db.execute(
                select(IngestionJob.id).where(
                    IngestionJob.document_id == document.id,
                    IngestionJob.status.in_(("queued", "running")),
                )
            )
        ).first()
        if live is not None:
            continue
        document.status = "failed"
        document.progress = None
        document.failure_reason = "Ingestion stopped unexpectedly"
        document.failure_detail = (
            "The process handling this document stopped before it finished. "
            "Retry to run ingestion again."
        )
        marked += 1
    return marked
