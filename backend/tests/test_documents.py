"""Upload, validation, the library, and real ingestion.

These upload genuine files — a real PDF with a real text layer, a real DOCX with
a table — because the §7.1 decision only pays off if a span resolves against
text pulled from a file the user actually supplied.
"""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import ChunkMeta, DocumentPage, DocumentSpan, UsageTransaction
from tests.conftest import FIXTURES, drain_ingestion, upload

# ------------------------------------------------------------- FR-2 rejection


async def test_unsupported_type_is_rejected_by_name(auth: AsyncClient) -> None:
    result = await upload(auth, FIXTURES / "slides.pptx")
    assert result["accepted"] == []
    reason = result["rejected"][0]["reason"]
    assert ".pptx is not supported" in reason
    assert "PDF, DOCX, TXT" in reason, "FR-2: say what *is* accepted"


async def test_mislabelled_file_is_rejected_on_its_bytes(auth: AsyncClient) -> None:
    """TS-4: the extension and Content-Type both come from the client."""
    result = await upload(auth, FIXTURES / "fake.pdf")
    assert result["accepted"] == []
    assert "not a PDF" in result["rejected"][0]["reason"]


async def test_empty_file_is_rejected(auth: AsyncClient) -> None:
    result = await upload(auth, FIXTURES / "empty.txt")
    assert "empty (0 bytes)" in result["rejected"][0]["reason"]


async def test_oversize_file_names_the_limit_and_the_value(
    auth: AsyncClient, tmp_path: Path
) -> None:
    """FR-2's actual requirement: both numbers, or the message is useless."""
    big = tmp_path / "huge.pdf"
    big.write_bytes(b"%PDF-1.4\n" + b"0" * (51 * 1024 * 1024))
    result = await upload(auth, big)
    reason = result["rejected"][0]["reason"]
    assert "51.0 MB" in reason, "the actual value is missing"
    assert "50.0 MB" in reason, "the limit is missing"


async def test_one_rejection_never_fails_the_batch(auth: AsyncClient) -> None:
    """FR-3: one failure must not fail the batch."""
    result = await upload(
        auth,
        FIXTURES / "nda.pdf",
        FIXTURES / "slides.pptx",
        FIXTURES / "amendment.docx",
        FIXTURES / "fake.pdf",
        FIXTURES / "notes.txt",
    )
    assert len(result["accepted"]) == 3
    assert len(result["rejected"]) == 2
    assert {d["name"] for d in result["accepted"]} == {
        "nda.pdf",
        "amendment.docx",
        "notes.txt",
    }


async def test_duplicate_upload_is_deduped_by_content_hash(auth: AsyncClient) -> None:
    first = await upload(auth, FIXTURES / "nda.pdf")
    second = await upload(auth, FIXTURES / "nda.pdf")
    assert first["accepted"][0]["id"] == second["accepted"][0]["id"]
    assert len((await auth.get("/api/documents")).json()) == 1


# ---------------------------------------------------------- real ingestion


async def test_pdf_ingests_to_ready_with_real_text(auth: AsyncClient, worker: None) -> None:
    result = await upload(auth, FIXTURES / "nda.pdf")
    document_id = result["accepted"][0]["id"]
    assert result["accepted"][0]["status"] == "queued"

    await drain_ingestion()
    document = (await auth.get(f"/api/documents/{document_id}")).json()

    assert document["status"] == "ready", document.get("failure")
    assert document["pages"] == 2
    assert document["type"] == "contract", "a filename and body like this is a contract"
    assert document["classifierConfidence"] > 0.7

    page = (await auth.get(f"/api/documents/{document_id}/pages/2")).json()
    assert "seven (7) years" in page["text"], "real pypdf extraction"
    assert "State of Ohio" in page["text"]


async def test_docx_table_survives_extraction(auth: AsyncClient, worker: None) -> None:
    """A payment schedule rendered as a table is exactly what gets asked about."""
    document_id = (await upload(auth, FIXTURES / "amendment.docx"))["accepted"][0]["id"]
    await drain_ingestion()

    page = (await auth.get(f"/api/documents/{document_id}/pages/1")).json()
    assert "$12,500" in page["text"], "table cells were dropped"
    assert "Enterprise" in page["text"]
    assert "forty-five (45) days" in page["text"]


async def test_ingestion_reports_every_step(auth: AsyncClient, worker: None) -> None:
    """FR-3: the status machine is the contract the upload queue polls."""
    document_id = (await upload(auth, FIXTURES / "nda.pdf"))["accepted"][0]["id"]
    await drain_ingestion()

    status = (await auth.get(f"/api/documents/{document_id}/status")).json()
    labels = [s["label"] for s in status["ingestion"]]
    # The last label names what step 6 actually did. The suite pins
    # LL_INDEXER=stub, so no vectors are written and the step says so —
    # "Embedded & indexed" over an empty collection would read as success to
    # anyone not counting the points. `test_ingestion_indexing.py` asserts the
    # other label against a real indexer with a fake store.
    assert labels == [
        "Uploaded",
        "Text extracted",
        "Layout QA passed",
        "Classified",
        "Chunked",
        "Indexed without vectors",
    ]
    assert all(s["status"] == "done" for s in status["ingestion"])
    assert status["progress"] == 100
    assert "not retrievable" in status["ingestion"][-1]["detail"]


async def test_ingestion_writes_pages_spans_and_chunks(auth: AsyncClient, worker: None) -> None:
    """FR-4: text with position metadata sufficient to resolve a citation."""
    document_id = (await upload(auth, FIXTURES / "nda.pdf"))["accepted"][0]["id"]
    await drain_ingestion()

    async with get_sessionmaker()() as db:
        pages = list(
            (
                await db.execute(
                    select(DocumentPage).where(DocumentPage.document_id == document_id)
                )
            ).scalars()
        )
        spans = list(
            (
                await db.execute(
                    select(DocumentSpan).where(DocumentSpan.document_id == document_id)
                )
            ).scalars()
        )
        chunks = list(
            (
                await db.execute(select(ChunkMeta).where(ChunkMeta.document_id == document_id))
            ).scalars()
        )

    assert len(pages) == 2
    assert all(p.char_count > 0 for p in pages)
    assert spans, "no spans means no resolvable citation"
    assert chunks, "no chunk_meta means nothing for the re-index worker to walk"
    # LL_INDEXER=stub in this suite, so nothing was embedded and the column is
    # NULL. With a real indexer it holds the Qdrant point id — see
    # `test_ingestion_indexing.py::test_point_ids_land_on_chunk_meta_rows`.
    assert all(c.vector_point_id is None for c in chunks)
    assert all(c.index_version for c in chunks)
    # Parents and children both get a row, and a child points at its parent.
    # Without the parent rows there is nothing for parent expansion to fetch.
    assert any(c.parent_chunk_id for c in chunks), "no child is linked to a parent section"


async def test_span_offsets_are_page_local(auth: AsyncClient, worker: None) -> None:
    """BACKEND-TODO §8.2: offsets index into *page* text, not the document.

    Getting this wrong puts every highlight in the wrong place and looks like a
    frontend bug for a day, so it is asserted directly.
    """
    document_id = (await upload(auth, FIXTURES / "nda.pdf"))["accepted"][0]["id"]
    await drain_ingestion()

    async with get_sessionmaker()() as db:
        spans = list(
            (
                await db.execute(
                    select(DocumentSpan).where(DocumentSpan.document_id == document_id)
                )
            ).scalars()
        )

    for span in spans:
        page = (await auth.get(f"/api/documents/{document_id}/pages/{span.page_no}")).json()
        text = page["text"]
        assert span.char_start < span.char_end <= len(text), (
            f"span {span.id} [{span.char_start}:{span.char_end}] does not fit page "
            f"{span.page_no} of {len(text)} chars — offsets are not page-local"
        )
        assert text[span.char_start : span.char_end].strip()


async def test_unreadable_pdf_fails_with_an_actionable_reason(
    auth: AsyncClient, worker: None
) -> None:
    """A PDF with no usable text layer is quarantined, not indexed with nothing.

    Indexing it would make it answerable-but-empty, which is worse than a
    visible failure (BRD §15).
    """
    document_id = (await upload(auth, FIXTURES / "scanned.pdf"))["accepted"][0]["id"]
    await drain_ingestion()

    document = (await auth.get(f"/api/documents/{document_id}")).json()
    assert document["status"] == "failed"
    assert document["failure"]["reason"]
    assert document["failure"]["detail"], "FR-28: a failure names a next action"
    steps = {s["label"]: s["status"] for s in document["ingestion"]}
    assert "failed" in steps.values()


async def test_failed_document_can_be_retried(auth: AsyncClient, worker: None) -> None:
    document_id = (await upload(auth, FIXTURES / "scanned.pdf"))["accepted"][0]["id"]
    await drain_ingestion()
    assert (await auth.get(f"/api/documents/{document_id}")).json()["status"] == "failed"

    response = await auth.post(f"/api/documents/{document_id}/retry")
    assert response.status_code == 200
    assert response.json()["status"] == "queued"

    await drain_ingestion()
    # Deterministically unreadable, so it fails again — and says so rather than
    # silently flipping to ready.
    assert (await auth.get(f"/api/documents/{document_id}")).json()["status"] == "failed"


async def test_ingestion_records_a_cost_row(auth: AsyncClient, worker: None) -> None:
    """NFR-8: ≤ $0.50 per document ingested, attributed per request."""
    document_id = (await upload(auth, FIXTURES / "nda.pdf"))["accepted"][0]["id"]
    await drain_ingestion()

    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(
                    select(UsageTransaction).where(UsageTransaction.document_id == document_id)
                )
            ).scalars()
        )
    assert rows, "ingestion cost was not recorded"
    assert rows[0].kind == "ingestion"
    assert rows[0].usd <= 0.50, "NFR-8 budget exceeded"


# ------------------------------------------------------------------ library


async def test_library_filters_and_sorts(corpus: AsyncClient) -> None:
    """FR-6: filterable and sortable."""
    contracts = (await corpus.get("/api/documents?type=contract")).json()
    assert contracts and all(d["type"] == "contract" for d in contracts)

    ready = (await corpus.get("/api/documents?status=ready")).json()
    assert ready and all(d["status"] == "ready" for d in ready)

    by_name = (await corpus.get("/api/documents?sort=name_asc")).json()
    assert [d["name"] for d in by_name] == sorted(d["name"] for d in by_name)


async def test_type_override_records_its_provenance(corpus: AsyncClient) -> None:
    """FR-5: show the type and allow override — and say which it is."""
    before = (await corpus.get("/api/documents/doc_depo")).json()
    assert before["typeSource"] == "detected"

    response = await corpus.patch("/api/documents/doc_depo", json={"type": "pleading"})
    assert response.status_code == 200
    after = response.json()
    assert after["type"] == "pleading"
    assert after["typeSource"] == "overridden"


async def test_ocr_low_confidence_pages_are_surfaced(corpus: AsyncClient) -> None:
    """DR-1: OCR confidence recorded and low-confidence pages flagged."""
    document = (await corpus.get("/api/documents/doc_amendment")).json()
    assert document["ocr"]["scanned"] is True
    assert document["ocr"]["lowConfidencePages"] == [3, 4]

    flagged = (await corpus.get("/api/documents/doc_amendment/pages/3")).json()
    assert flagged["ocrConfidence"] is not None
    assert flagged["ocrConfidence"] < 0.7

    clean = (await corpus.get("/api/documents/doc_amendment/pages/2")).json()
    assert clean["ocrConfidence"] is None


async def test_workspace_health_counts_by_status(corpus: AsyncClient) -> None:
    health = (await corpus.get("/api/workspace/health")).json()
    assert health["documents"] == 8
    assert health["ready"] == 6
    assert health["indexing"] == 1
    assert health["failed"] == 1
    assert health["pages"] > 0


async def test_page_request_is_cacheable(corpus: AsyncClient) -> None:
    """The 500-page viewer hammers this endpoint."""
    response = await corpus.get("/api/documents/doc_msa/pages/84")
    assert response.headers.get("etag")
    assert "private" in response.headers.get("cache-control", "")


async def test_missing_page_is_a_404_not_an_empty_page(corpus: AsyncClient) -> None:
    response = await corpus.get("/api/documents/doc_msa/pages/9999")
    assert response.status_code == 404
    assert response.json()["action"]
