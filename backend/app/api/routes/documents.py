"""Documents: upload, library, status polling, type override, retry, delete.

The upload handler streams each file to disk rather than buffering it. A 50 MB
limit times a multi-file drop is enough memory to matter, and `UploadFile.read()`
on the whole body is the usual way that becomes a production incident.

Per-file outcomes are the contract (FR-3): one rejected file never fails the
batch, and each rejection names the limit *and* the actual value (FR-2).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, File, Query, Request, Response, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api import errors
from app.api.deps import CurrentUser, DbSession, client_ip
from app.config import get_settings
from app.ingestion import queue, validate
from app.models import Document, DocumentPage, IngestionJob
from app.schemas.documents import (
    DocumentStatus,
    DocumentTypePatch,
    LegalDocument,
    Page,
    RejectedFile,
    UploadResult,
)
from app.services import audit
from app.services import documents as service
from app.storage import blob, signed_url

log = logging.getLogger("legallens.documents.api")

router = APIRouter(prefix="/api/documents", tags=["documents"])

CHUNK_BYTES = 1024 * 1024


@router.post("", response_model=UploadResult, status_code=status.HTTP_201_CREATED)
async def upload(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    files: list[UploadFile] = File(..., description="One or more PDF, DOCX or TXT files"),
) -> UploadResult:
    settings = get_settings()
    accepted: list[LegalDocument] = []
    rejected: list[RejectedFile] = []

    existing_count = await service.count_documents(db, user.id)

    for upload_file in files:
        name = Path(upload_file.filename or "untitled").name
        if existing_count + len(accepted) >= settings.max_documents:
            rejected.append(
                RejectedFile(
                    name=name,
                    reason=(
                        f"Your workspace already holds {existing_count + len(accepted)} "
                        f"documents, at the {settings.max_documents}-document limit. "
                        "Delete one to make room."
                    ),
                )
            )
            continue

        document, job_rows, rejection = await _ingest_one(db, user.id, upload_file, name)
        if rejection is not None:
            rejected.append(rejection)
            audit.record(
                db,
                action=audit.ACTION_UPLOAD_REJECTED,
                user_id=user.id,
                detail={"name": name, "reason": rejection.reason},
                ip=client_ip(request),
            )
            continue

        assert document is not None
        accepted.append(service.to_schema(document, jobs=job_rows))

    await db.commit()
    return UploadResult(accepted=accepted, rejected=rejected)


async def _ingest_one(
    db: DbSession, user_id: str, upload_file: UploadFile, name: str
) -> tuple[Document | None, list[IngestionJob], RejectedFile | None]:
    """One file's journey from multipart part to a queued `documents` row.

    Returns either (document, jobs, None) or (None, [], rejection) — never both
    and never neither, so the caller's batch accounting cannot drift.
    """
    settings = get_settings()

    # Cheapest first: a `.pptx` never gets written anywhere.
    rejection = validate.validate_extension(name)
    if rejection is not None:
        return None, [], RejectedFile(name=name, reason=rejection.reason)

    # The client-declared size is a hint, not a fact — it lets an oversized
    # upload be refused without streaming it, but the real check is `written`
    # below, against the bytes that actually arrived.
    if upload_file.size:
        rejection = validate.validate_size(upload_file.size)
        if rejection is not None:
            return None, [], RejectedFile(name=name, reason=rejection.reason)

    temp_path = await asyncio.to_thread(_make_temp, settings.data_dir, validate.extension_of(name))
    written = 0
    try:
        # Disk writes go to a thread: a 50 MB upload on the event loop would
        # stall every in-flight SSE stream, which is the most likely way this
        # backend would look broken under a multi-file drop.
        while chunk := await upload_file.read(CHUNK_BYTES):
            written += len(chunk)
            if written > settings.max_upload_bytes:
                # Stop reading rather than finish buffering a file we have
                # already decided to refuse.
                return (
                    None,
                    [],
                    RejectedFile(
                        name=name,
                        reason=(
                            f"{written / 1024 / 1024:.1f} MB exceeds the "
                            f"{settings.max_upload_bytes / 1024 / 1024:.0f} MB limit."
                        ),
                    ),
                )
            await asyncio.to_thread(_append, temp_path, chunk)

        size_rejection = validate.validate_size(written)
        if size_rejection is not None:
            return None, [], RejectedFile(name=name, reason=size_rejection.reason)

        content_rejection = await asyncio.to_thread(validate.validate_content, temp_path, name)
        if content_rejection is not None:
            return None, [], RejectedFile(name=name, reason=content_rejection.reason)

        sha256 = await asyncio.to_thread(blob.sha256_file, temp_path)
        duplicate = await service.find_duplicate(db, user_id, sha256)
        if duplicate is not None:
            # Not a rejection: the user gets the document they asked for. No new
            # job, because nothing new needs ingesting.
            return duplicate, [], None

        ext = validate.extension_of(name)
        document = Document(
            user_id=user_id,
            name=name[:500],
            ext=ext,
            mime=validate.EXT_MIME.get(ext, "application/octet-stream"),
            sha256=sha256,
            size_bytes=written,
            pages=0,
            status="queued",
            progress=0,
        )
        db.add(document)
        await db.flush()

        key = blob.object_key("files", document.id, "original")
        await asyncio.to_thread(_encrypt_into, key, temp_path)
        document.blob_key = key

        job = await queue.enqueue(db, document)
        audit.record(
            db,
            action=audit.ACTION_UPLOAD,
            user_id=user_id,
            document_ids=[document.id],
            detail={"name": document.name, "bytes": written, "sha256": sha256[:16]},
        )
        await db.flush()
        return document, [job], None
    finally:
        await asyncio.to_thread(temp_path.unlink, True)


# Blocking helpers, always called through `asyncio.to_thread`.


def _make_temp(directory: Path, suffix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(delete=False, dir=directory, suffix=suffix) as handle:
        return Path(handle.name)


def _append(path: Path, chunk: bytes) -> None:
    with path.open("ab") as handle:
        handle.write(chunk)


def _encrypt_into(key: str, source: Path) -> None:
    blob.write(key, source.read_bytes())


@router.get("", response_model=list[LegalDocument])
async def list_documents(
    user: CurrentUser,
    db: DbSession,
    type: str | None = Query(default=None, pattern="^(contract|opinion|pleading|other)$"),
    status_filter: str | None = Query(default=None, alias="status"),
    sort: str = Query(default="uploaded_desc"),
) -> list[LegalDocument]:
    documents = await service.list_documents(
        db, user.id, doc_type=type, status=status_filter, sort=sort
    )
    return [service.to_schema(d, jobs=d.jobs) for d in documents]


@router.get("/{document_id}", response_model=LegalDocument)
async def get_document(document_id: str, user: CurrentUser, db: DbSession) -> LegalDocument:
    document = await service.get_document(db, user.id, document_id)
    if document is None:
        raise errors.not_found("That document")
    return service.to_schema(document, jobs=document.jobs)


@router.get("/{document_id}/status", response_model=DocumentStatus)
async def get_status(document_id: str, user: CurrentUser, db: DbSession) -> DocumentStatus:
    """The poll target for the upload queue. Deliberately small — the library
    polls this per in-flight file, and a full document payload per tick would
    be wasteful."""
    document = await service.get_document(db, user.id, document_id)
    if document is None:
        raise errors.not_found("That document")
    return service.to_status(document, jobs=document.jobs)


@router.get("/{document_id}/pages/{page_no}", response_model=Page)
async def get_page(
    document_id: str, page_no: int, user: CurrentUser, db: DbSession, response: Response
) -> Page:
    """FR-4 / FR-13. The 500-page viewer hammers this, so it carries an ETag
    and a private cache window — page text does not change once ingested."""

    document = await service.get_document(db, user.id, document_id)
    if document is None:
        raise errors.not_found("That document")

    page = (
        await db.execute(
            select(DocumentPage).where(
                DocumentPage.document_id == document_id, DocumentPage.page_no == page_no
            )
        )
    ).scalar_one_or_none()
    if page is None:
        raise errors.not_found(f"Page {page_no} of that document")

    text = ""
    if page.text_key:
        try:
            text = blob.read_text(page.text_key)
        except (FileNotFoundError, blob.BlobDecryptError) as exc:
            log.error("page text unreadable for %s p%s: %s", document_id, page_no, exc)
            raise errors.parse_failed(
                document_id,
                "The extracted text for this page could not be read back.",
            ) from exc

    response.headers["ETag"] = f'"{document_id}-{page_no}-{page.char_count}"'
    response.headers["Cache-Control"] = "private, max-age=300"
    return Page(
        document_id=document_id,
        page=page_no,
        text=text,
        ocr_confidence=page.ocr_confidence,
    )


@router.get("/{document_id}/pages/{page_no}/image")
async def get_page_image(
    document_id: str, page_no: int, user: CurrentUser, db: DbSession
) -> Response:
    """⊕ Page images for the citation viewer, behind a 60-second signed URL —
    never a guessable path (architecture band 6)."""

    document = await service.get_document(db, user.id, document_id)
    if document is None:
        raise errors.not_found("That document")

    page = (
        await db.execute(
            select(DocumentPage).where(
                DocumentPage.document_id == document_id, DocumentPage.page_no == page_no
            )
        )
    ).scalar_one_or_none()
    if page is None or not page.image_key:
        # Page rendering is M4's (it arrives with PDF.js on the frontend side).
        # Saying so beats a 500 or an empty image.
        raise errors.not_found(
            f"A rendered image for page {page_no}. Page rendering is not enabled yet"
        )

    token = signed_url.sign(page.image_key, user.id)
    return RedirectResponse(
        url=f"/api/blobs/{token}", status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


@router.patch("/{document_id}", response_model=LegalDocument)
async def patch_document(
    document_id: str,
    body: DocumentTypePatch,
    request: Request,
    user: CurrentUser,
    db: DbSession,
) -> LegalDocument:
    document = await service.get_document(db, user.id, document_id)
    if document is None:
        raise errors.not_found("That document")

    previous = document.type
    await service.set_type(db, document, body.type)
    audit.record(
        db,
        action=audit.ACTION_TYPE_OVERRIDE,
        user_id=user.id,
        document_ids=[document.id],
        detail={"from": previous, "to": body.type},
        ip=client_ip(request),
    )
    return service.to_schema(document, jobs=document.jobs)


@router.post("/{document_id}/retry", response_model=DocumentStatus)
async def retry_document(document_id: str, user: CurrentUser, db: DbSession) -> DocumentStatus:
    """⊕ FR-3's retry. Re-queues rather than mutating the failed job, so the
    previous attempt's log survives for comparison."""
    document = await service.get_document(db, user.id, document_id)
    if document is None:
        raise errors.not_found("That document")
    if document.status in service.IN_FLIGHT:
        raise errors.bad_request(
            "This document is already being processed.",
            "Wait for the current attempt to finish before retrying.",
        )
    if not document.blob_key or not blob.exists(document.blob_key):
        raise errors.bad_request(
            "The original file is no longer stored, so ingestion cannot be re-run.",
            "Upload the file again.",
        )

    document.failure_reason = None
    document.failure_detail = None
    document.ocr_json = None
    job = await queue.enqueue(db, document)
    await db.commit()
    return service.to_status(document, jobs=[job])


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str, request: Request, user: CurrentUser, db: DbSession
) -> Response:
    """FR-26: file, extracted text, chunks and embeddings, effective
    immediately for retrieval. TS-6: verified by `tests/test_hard_delete.py`."""
    document = await service.get_document(db, user.id, document_id)
    if document is None:
        raise errors.not_found("That document")

    name = document.name
    report = await service.hard_delete(db, document)
    # DR-4: the tombstone is the only thing a hard delete leaves behind.
    audit.record(
        db,
        action=audit.ACTION_DOC_DELETE,
        user_id=user.id,
        document_ids=[report.document_id],
        detail={"name": name, **report.as_detail()},
        ip=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
