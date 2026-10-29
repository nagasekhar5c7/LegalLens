"""FR-26 and TS-6.

TS-6 says deletion is real and *verified by test, not asserted*. This module is
therefore the requirement, not a check on it: if these tests are deleted, FR-26
is no longer satisfied regardless of what the code does.

What "real" means here, enumerated: the encrypted original, every page's
encrypted extracted text, the span map, page images, `chunk_meta` rows, the
ingestion job, the document row, and (at M4) the vector points. Effective
immediately for retrieval. An audit tombstone is the only survivor (DR-4).
"""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import func, select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import (
    AuditLog,
    ChunkMeta,
    Document,
    DocumentPage,
    DocumentSpan,
    IngestionJob,
)
from tests.conftest import WORKSPACE_SCOPE, collect_stream, drain_ingestion

PDF_FIXTURE = Path(__file__).parent / "fixtures" / "nda.pdf"


async def _counts(document_id: str) -> dict[str, int]:
    async with get_sessionmaker()() as db:

        async def count(model, column) -> int:
            return int(
                (
                    await db.execute(
                        select(func.count()).select_from(model).where(column == document_id)
                    )
                ).scalar_one()
            )

        return {
            "documents": await count(Document, Document.id),
            "document_pages": await count(DocumentPage, DocumentPage.document_id),
            "spans": await count(DocumentSpan, DocumentSpan.document_id),
            "chunk_meta": await count(ChunkMeta, ChunkMeta.document_id),
            "ingestion_jobs": await count(IngestionJob, IngestionJob.document_id),
        }


def _blobs(document_id: str) -> list[str]:
    root = get_settings().blobs_dir
    if not root.exists():
        return []
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and document_id in p.as_posix()
    )


async def test_hard_delete_removes_every_trace(
    auth: AsyncClient, worker: None, nda_pdf: Path
) -> None:
    """The enumeration test. Upload, ingest, delete, then assert on every table
    and on the blob store directly rather than trusting the endpoint's word."""
    with nda_pdf.open("rb") as handle:
        response = await auth.post(
            "/api/documents", files={"files": (nda_pdf.name, handle, "application/pdf")}
        )
    assert response.status_code == 201, response.text
    document_id = response.json()["accepted"][0]["id"]
    await drain_ingestion()

    before = await _counts(document_id)
    assert before["documents"] == 1
    assert before["document_pages"] > 0, "nothing was ingested, so nothing is proven"
    assert before["spans"] > 0
    assert before["chunk_meta"] > 0
    assert _blobs(document_id), "the encrypted original should be on disk"

    assert (await auth.delete(f"/api/documents/{document_id}")).status_code == 204

    after = await _counts(document_id)
    assert after == dict.fromkeys(before, 0), f"rows survived the delete: {after}"
    assert _blobs(document_id) == [], "ciphertext survived the delete"


async def test_delete_is_effective_immediately_for_reads(
    auth: AsyncClient, worker: None, nda_pdf: Path
) -> None:
    """FR-26: "effective immediately for retrieval" — no eventual consistency."""
    with nda_pdf.open("rb") as handle:
        accepted = (
            await auth.post(
                "/api/documents", files={"files": (nda_pdf.name, handle, "application/pdf")}
            )
        ).json()["accepted"][0]
    document_id = accepted["id"]
    await drain_ingestion()

    async with get_sessionmaker()() as db:
        span_id = (
            (
                await db.execute(
                    select(DocumentSpan.id).where(DocumentSpan.document_id == document_id)
                )
            )
            .scalars()
            .first()
        )
    assert span_id

    assert (await auth.get(f"/api/documents/{document_id}")).status_code == 200
    assert (await auth.get(f"/api/spans/{span_id}")).status_code == 200

    await auth.delete(f"/api/documents/{document_id}")

    assert (await auth.get(f"/api/documents/{document_id}")).status_code == 404
    assert (await auth.get(f"/api/documents/{document_id}/pages/1")).status_code == 404
    assert (await auth.get(f"/api/spans/{span_id}")).status_code == 404
    listed = {d["id"] for d in (await auth.get("/api/documents")).json()}
    assert document_id not in listed


async def test_deleted_document_leaves_retrieval_scope_at_once(
    corpus: AsyncClient,
) -> None:
    """A deleted document must not be searchable on the very next turn."""
    before = await collect_stream(
        corpus,
        "/api/ask",
        {"text": "What is the liability cap?", "mode": "ask", "scope": WORKSPACE_SCOPE},
    )
    scope_before = next(e for e in before if e["type"] == "scope")["scopeUsed"]
    assert "doc_msa" in scope_before["documentIds"]

    assert (await corpus.delete("/api/documents/doc_msa")).status_code == 204

    after = await collect_stream(
        corpus,
        "/api/ask",
        {"text": "What is the liability cap?", "mode": "ask", "scope": WORKSPACE_SCOPE},
    )
    scope_after = next(e for e in after if e["type"] == "scope")["scopeUsed"]
    assert "doc_msa" not in scope_after["documentIds"]
    assert scope_after["documentCount"] == scope_before["documentCount"] - 1
    # And nothing it used to supply may be cited any more.
    cited = {e["citation"]["documentId"] for e in after if e["type"] == "citation"}
    assert "doc_msa" not in cited


async def test_delete_keeps_an_audit_tombstone(
    auth: AsyncClient, worker: None, nda_pdf: Path
) -> None:
    """DR-4: the audit trail outlives the document, and records what went."""
    with nda_pdf.open("rb") as handle:
        document_id = (
            await auth.post(
                "/api/documents", files={"files": (nda_pdf.name, handle, "application/pdf")}
            )
        ).json()["accepted"][0]["id"]
    await drain_ingestion()
    await auth.delete(f"/api/documents/{document_id}")

    async with get_sessionmaker()() as db:
        tombstone = (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.action == "doc.hard_delete")
                .order_by(AuditLog.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    assert tombstone is not None, "a hard delete with no audit row is unaccountable"
    assert tombstone.document_ids_json == [document_id]
    detail = tombstone.detail_json or {}
    # The tombstone enumerates what was removed, so the claim is auditable
    # without re-deriving it.
    for key in ("chunks", "spans", "pages", "blobs", "vector_points"):
        assert key in detail, f"tombstone does not account for {key}"
    assert detail["errors"] == [], f"delete reported errors: {detail['errors']}"


async def test_workspace_delete_requires_confirmation(corpus: AsyncClient) -> None:
    response = await corpus.request("DELETE", "/api/workspace", json={"confirm": "yes"})
    assert response.status_code == 400
    assert "DELETE" in response.json()["message"]
    # Nothing may have been removed by a refused request.
    assert len((await corpus.get("/api/documents")).json()) > 0


async def test_workspace_delete_removes_everything_but_the_audit_log(
    corpus: AsyncClient,
) -> None:
    await collect_stream(
        corpus,
        "/api/ask",
        {"text": "What is the liability cap?", "mode": "ask", "scope": WORKSPACE_SCOPE},
    )

    response = await corpus.request("DELETE", "/api/workspace", json={"confirm": "DELETE"})
    assert response.status_code == 204

    assert (await corpus.get("/api/documents")).json() == []
    assert (await corpus.get("/api/conversations")).json()["items"] == []
    health = (await corpus.get("/api/workspace/health")).json()
    assert health == {"documents": 0, "ready": 0, "indexing": 0, "failed": 0, "pages": 0}

    async with get_sessionmaker()() as db:
        audit_rows = int(
            (await db.execute(select(func.count()).select_from(AuditLog))).scalar_one()
        )
        remaining_pages = int(
            (await db.execute(select(func.count()).select_from(DocumentPage))).scalar_one()
        )
        remaining_spans = int(
            (await db.execute(select(func.count()).select_from(DocumentSpan))).scalar_one()
        )
    assert remaining_pages == 0
    assert remaining_spans == 0
    assert audit_rows > 0, "the audit log must survive a workspace deletion (DR-4)"

    # And the blob store is swept, not just dereferenced.
    root = get_settings().blobs_dir
    leftover = [p for p in root.rglob("*") if p.is_file()] if root.exists() else []
    assert leftover == [], f"ciphertext survived the workspace delete: {leftover}"


async def test_blob_store_has_no_orphans_after_delete(
    auth: AsyncClient, worker: None, nda_pdf: Path
) -> None:
    """The reconciliation path: a file whose key was never recorded still goes."""
    with nda_pdf.open("rb") as handle:
        document_id = (
            await auth.post(
                "/api/documents", files={"files": (nda_pdf.name, handle, "application/pdf")}
            )
        ).json()["accepted"][0]["id"]
    await drain_ingestion()

    # A page image nobody wrote a DB row for — exactly what an interrupted
    # render would leave behind.
    from app.storage import blob

    blob.write(f"images/{document_id}/p1.png.enc", b"not-a-real-png")
    assert blob.exists(f"images/{document_id}/p1.png.enc")

    await auth.delete(f"/api/documents/{document_id}")
    assert not blob.exists(f"images/{document_id}/p1.png.enc")
    assert _blobs(document_id) == []
