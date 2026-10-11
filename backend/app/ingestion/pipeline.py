"""The six ingestion steps of architecture band 7, each a port call.

    (1) upload & validate    → app/ingestion/validate.py, at the route
    (2) job queue            → app/ingestion/queue.py
    (3) parse: layout + OCR  → extract/* (text layer) · ocr/* (scanned)
    (4) layout QA gate       → stub/layout_qa
    (5) classify · segment · chunk
    (6) embed & index        → app/indexing.py, then status = ready

Step 3 is where the two paths the product actually has diverge, and they
converge again immediately:

    PDF with a text layer ─┐
    DOCX                   ├─→ chunk → embed → vector store → ready
    TXT                    │
    scanned PDF → OCR ─────┘

The branch is decided by measurement, not by file type: `has_no_text_layer`
compares mean characters per page against `LL_TEXT_LAYER_CHARS_PER_PAGE_FLOOR`.
A scanned fax yields a handful of stray characters per page rather than zero, so
a `== 0` check would classify it as machine-readable and index the garbage.

**The gate is per document; the work inside it is per page.** Once a document is
on the OCR branch, only its below-floor pages are recognised, so a part-scanned
file keeps the text-layer extraction for the pages that had one — recognition is
strictly worse than a real text layer and must not replace it.

**Known gap, stated rather than glossed:** a mostly born-digital document with a
few scanned exhibits has a *mean* above the floor, so it never enters the branch
and those exhibits go unread. The alternative — triggering on any below-floor
page — was rejected because blank separator pages are common in filed PDFs, and
it would rasterise and "OCR" them, label every such contract `scanned`, and bill
it at OCR rates. Fixing this properly needs a per-page classifier that can tell
an image-only page from an intentionally sparse one, which is the same
layout-analysis work BRD §15 defers.

Everything downstream of step 3 is shared. Chunking, embedding and indexing do
not know or care which branch produced the text; what they get is
`ParsedPage[]`, and the only trace of the difference is the per-page
`ocr_confidence` that the DR-1 gate reads and the `parse_engine` recorded
against each page.

The status machine is the contract the frontend's upload queue polls, and
`steps_json` is shaped as `IngestionStep[]` so the detail drawer renders it
without translation.

Everything CPU-bound — pypdf, python-docx, rasterising, OCR, embedding,
chunking, AES — goes through `asyncio.to_thread`. Everything touching the
database stays on the caller's loop, because an async engine is bound to the
loop that created it. Getting that split wrong is how a 500-page parse stalls
every in-flight SSE stream.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.ingestion.extract import parser_for
from app.ingestion.ports import Chunk, DocumentRef, IndexOutcome, ParsedPage, ParseResult
from app.ingestion.stub import (
    SectionAwareChunker,
    StubClassifier,
    StubLayoutQa,
)
from app.models import (
    ChunkMeta,
    Document,
    DocumentPage,
    DocumentSpan,
    IndexVersion,
    IngestionJob,
    UsageTransaction,
)
from app.models.base import prefixed_id, utcnow
from app.services import audit
from app.storage import blob

log = logging.getLogger("legallens.ingest")

LAYOUT_QA = StubLayoutQa()
CLASSIFIER = StubClassifier()
CHUNKER = SectionAwareChunker()


def get_ocr() -> Any:
    """The configured OCR engine, resolved per call.

    Not a module-level singleton, unlike the chunker: the engines are cheap to
    construct (the ONNX session and the tesseract process are created on use),
    and resolving per job means a settings change takes effect without a
    restart. `app/indexing.py` caches its indexer for the opposite reason — that
    one owns a Qdrant directory lock.
    """
    settings = get_settings()
    from app.ingestion.ocr import build_engine

    return build_engine(settings.ocr_engine, dpi=settings.ocr_dpi, languages=settings.ocr_languages)


def get_indexer() -> Any:
    """The configured indexer, from the one module that knows both layers.

    Imported inside the function on purpose. `tests/test_layering.py` forbids
    this package from importing `app.ai`; `app.indexing` is the seam that is
    allowed to, and keeping the import here also keeps a backend with no
    retrieval extra from paying for the resolution at import time.
    """
    from app.indexing import get_indexer as resolve

    return resolve()


class IngestionFailure(Exception):
    """A terminal, reportable failure.

    Carries the two-part message the UI shows — a one-line reason and a detail
    naming a next action — plus the step log as it stood when the failure
    happened. The log travels with the exception because the failure handler
    runs in a *different* session: anything merely assigned to the job here
    would be rolled back and never reach the detail drawer.
    """

    def __init__(self, reason: str, detail: str, steps: list[dict[str, Any]] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.steps = steps or []


@dataclass
class StepLog:
    """Accumulates `IngestionStep[]` as the pipeline runs, so a document being
    processed shows real progress rather than an indeterminate spinner.

    Always persisted via `snapshot()`. A plain JSON column does not track
    in-place mutation, and assigning the same list object back is invisible to
    the ORM — so appends would silently never reach the database.
    """

    steps: list[dict[str, Any]]

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(s) for s in self.steps]

    def done(self, label: str, ms: float, detail: str | None = None) -> None:
        self.steps.append(
            {
                "label": label,
                "status": "done",
                "ms": int(ms),
                **({"detail": detail} if detail else {}),
            }
        )

    def active(self, label: str, detail: str | None = None) -> None:
        self.steps.append(
            {"label": label, "status": "active", **({"detail": detail} if detail else {})}
        )

    def failed(self, label: str, ms: float, detail: str | None = None) -> None:
        self.steps.append(
            {
                "label": label,
                "status": "failed",
                "ms": int(ms),
                **({"detail": detail} if detail else {}),
            }
        )

    def pending(self, *labels: str) -> None:
        for label in labels:
            self.steps.append({"label": label, "status": "pending"})

    def replace_active(self, label: str, ms: float, detail: str | None = None) -> None:
        for step in reversed(self.steps):
            if step["status"] == "active":
                step.update(
                    {"label": label, "status": "done", "ms": int(ms)},
                )
                if detail:
                    step["detail"] = detail
                return
        self.done(label, ms, detail)


async def run_pipeline(db: AsyncSession, job: IngestionJob, document: Document) -> None:
    """Run steps 3–6 for one document. Raises IngestionFailure on a terminal
    failure; any other exception is retried by the queue."""
    settings = get_settings()
    t0 = time.perf_counter()
    steps = StepLog(steps=list(job.steps_json or []))
    if not steps.steps:
        steps.done("Uploaded", 0)

    ocr = get_ocr()
    indexer = get_indexer()
    source = await asyncio.to_thread(_decrypt_to_temp, document)
    try:
        # ---- (3a) parse the text layer --------------------------------------
        document.status = "parsing"
        document.progress = 10
        job.step = "parsing"
        job.steps_json = steps.snapshot()
        await db.commit()

        parse_started = time.perf_counter()
        result = await asyncio.to_thread(_parse, source, document, settings.max_pages)
        parse_ms = (time.perf_counter() - parse_started) * 1000

        # ---- (3b) OCR the pages that have no text layer ---------------------
        if result.scanned:
            steps.done(
                "No text layer found",
                parse_ms,
                f"{result.total_chars:,} chars over {result.page_count} pages — "
                f"below the {settings.text_layer_chars_per_page_floor}/page floor",
            )
            document.status = "ocr"
            document.progress = 35
            job.step = "ocr"
            ocr_pages = _pages_needing_ocr(result, settings.text_layer_chars_per_page_floor)
            steps.active(f"OCR · {ocr.name}", f"{len(ocr_pages)} page(s)")
            job.steps_json = steps.snapshot()
            await db.commit()

            ocr_started = time.perf_counter()
            ocr_result = await asyncio.to_thread(
                _run_ocr, ocr, source, document, ocr_pages, result.page_count
            )
            result = _merge_ocr(result, ocr_result)
            mean = _mean_confidence(result.pages)
            flagged = [
                p.page_no
                for p in result.pages
                if p.ocr_confidence is not None and p.ocr_confidence < settings.ocr_confidence_floor
            ]
            steps.replace_active(
                f"OCR complete · {ocr.name}",
                (time.perf_counter() - ocr_started) * 1000,
                f"mean confidence {mean:.2f}"
                + (f" · {len(flagged)} page(s) flagged" if flagged else "")
                + (f" · {ocr_result.warnings[0]}" if ocr_result.warnings else ""),
            )
        else:
            steps.done(
                "Text extracted",
                parse_ms,
                f"{result.engine} · {result.total_chars:,} chars over {result.page_count} pages",
            )

        document.pages = result.page_count

        # ---- (4) layout QA gate --------------------------------------------
        qa_started = time.perf_counter()
        qa = await asyncio.to_thread(LAYOUT_QA.check, result)
        qa_ms = (time.perf_counter() - qa_started) * 1000

        if qa.quarantine:
            steps.failed(
                "Layout QA gate — quarantined",
                qa_ms,
                f"reading order {qa.reading_order_score:.2f}",
            )
            steps.pending("Chunked", "Embedded & indexed")
            raise IngestionFailure(
                reason=_quarantine_reason(result, qa),
                detail=qa.reason or "The document did not pass the layout quality gate.",
                steps=steps.snapshot(),
            )

        steps.done(
            "Layout QA passed",
            qa_ms,
            f"reading order {qa.reading_order_score:.2f}"
            + (f" · {len(qa.flagged_pages)} page(s) flagged" if qa.flagged_pages else ""),
        )

        # DR-1: OCR info is recorded whether or not any page was flagged, so the
        # library badge and the viewer's confidence strip have data to show.
        # `engine` is in here because a silent fallback to the no-OCR stub would
        # otherwise be indistinguishable from a clean scan after the fact.
        if result.scanned:
            document.ocr_json = {
                "scanned": True,
                "engine": ocr.name,
                "mean_confidence": round(_mean_confidence(result.pages), 2),
                "low_confidence_pages": qa.flagged_pages,
            }

        # ---- (5) classify · segment · chunk --------------------------------
        document.status = "indexing"
        document.progress = 60
        job.step = "indexing"
        job.steps_json = steps.snapshot()
        await db.commit()

        classify_started = time.perf_counter()
        classification = await asyncio.to_thread(CLASSIFIER.classify, document.name, result.pages)
        # FR-5: an override already made by the attorney outranks the classifier.
        if document.type_source != "overridden":
            document.type = classification.doc_type
        document.classifier_confidence = classification.confidence
        steps.done(
            "Classified",
            (time.perf_counter() - classify_started) * 1000,
            "low confidence — review the type"
            if classification.confidence < 0.7
            else f"{classification.doc_type} · {int(classification.confidence * 100)}%",
        )

        chunk_started = time.perf_counter()
        chunks = await asyncio.to_thread(CHUNKER.chunk, result.pages, document.type)
        children = [c for c in chunks if not c.is_parent]
        parents = [c for c in chunks if c.is_parent]
        steps.done(
            "Chunked",
            (time.perf_counter() - chunk_started) * 1000,
            f"{len(children)} chunks · {len(parents)} parent sections",
        )

        # ---- persist pages, images, spans and chunk rows --------------------
        images = await _persist_page_images(document, result, source, settings)
        await _persist_pages(db, document, result.pages, images)
        span_count = await _persist_spans(db, document, chunks)
        rows = await _persist_chunks(db, document, chunks, indexer.index_version)
        document.progress = 85

        # The step is marked active and committed *before* the embed, because
        # this is the slow one: bge-m3 on CPU runs roughly 5-15 chunks a second,
        # so a long document sits here for minutes. Without this the UI shows a
        # finished "Chunked" step and nothing else while the worker is busy.
        steps.active(
            "Embedding & indexing",
            f"{len(children)} chunks + {len(parents)} parents · {indexer.index_version}",
        )
        job.steps_json = steps.snapshot()
        await db.commit()

        # ---- (6) embed & index ---------------------------------------------
        index_started = time.perf_counter()
        ref = DocumentRef(id=document.id, name=document.name, type=document.type)
        # Parents go in with the children. They are never vector-searched — the
        # store filters them out of `search` — but parent expansion fetches them
        # by key, and a collection of children alone degrades the topology to
        # naive top-K without raising anything.
        outcome: IndexOutcome = await asyncio.to_thread(indexer.index, ref, chunks)
        await _record_point_ids(db, rows, outcome)
        await _register_index_version(db, outcome)
        steps.replace_active(
            "Embedded & indexed" if outcome.vectors_written else "Indexed without vectors",
            (time.perf_counter() - index_started) * 1000,
            _index_detail(outcome, span_count),
        )

        # FR-7: only now does the document enter retrieval.
        document.status = "ready"
        document.progress = 100
        document.failure_reason = None
        document.failure_detail = None
        job.status = "done"
        job.step = "ready"
        job.steps_json = steps.snapshot()
        job.finished_at = utcnow()

        # NFR-8: ≤ $0.50 per document ingested, attributed per request and
        # visible in `GET /api/usage`. A ledger row, not a log line — the
        # budget is only checkable if it can be summed.
        elapsed_ms = (time.perf_counter() - t0) * 1000
        db.add(
            UsageTransaction(
                user_id=document.user_id,
                document_id=document.id,
                kind="ingestion",
                # Tokens embedded, not tokens billed. bge-m3 runs locally, so
                # this column is a workload measure here rather than a price
                # input — see `_ingestion_cost`.
                input_tokens=sum(c.token_count for c in chunks),
                output_tokens=0,
                usd=_ingestion_cost(result.page_count, result.scanned),
                latency_ms=elapsed_ms,
                model=outcome.index_version,
            )
        )

        audit.record(
            db,
            action=audit.ACTION_INGEST_COMPLETE,
            user_id=document.user_id,
            document_ids=[document.id],
            index_version=outcome.index_version,
            detail={
                "pages": result.page_count,
                "chunks": len(children),
                "parents": len(parents),
                "spans": span_count,
                "scanned": result.scanned,
                "engine": result.engine,
                "ocr_engine": ocr.name if result.scanned else None,
                "index_engine": outcome.engine,
                "vectors_written": outcome.vectors_written,
                "points": outcome.written if outcome.vectors_written else 0,
                "embed_ms": int(outcome.embed_ms),
                "elapsed_ms": int(elapsed_ms),
                "warnings": [*result.warnings, *outcome.warnings][:5],
            },
        )
        await db.commit()
        log.info(
            "ingested %s: %d pages, %d chunks, %d spans, %d vector points in %.1fs",
            document.id,
            result.page_count,
            len(children),
            span_count,
            outcome.written if outcome.vectors_written else 0,
            time.perf_counter() - t0,
        )
    finally:
        await asyncio.to_thread(source.unlink, True)


#: Per-page cost model.
#:
#: Embedding is **local** — bge-m3 runs in this process — so there is no
#: per-token price to add here, and inventing one would make NFR-8's budget
#: look tighter than it is. What this covers is the parse, the OCR and the
#: storage: the compute and the bytes, amortised per page. The number to watch
#: on this path is wall clock, not dollars, and `IndexOutcome.embed_ms` is where
#: that is recorded.
#:
#: A hosted embedding API would change this: at roughly $0.02 per million tokens
#: a 500-page document is still well under a cent, so the budget holds either
#: way — but the line item would belong here rather than in a comment.
#:
#: Deliberately a function so the NFR-8 budget assertion in
#: `tests/test_documents.py` has one place to point at.
COST_PER_PAGE_PARSE_USD = 0.0004
COST_PER_PAGE_OCR_USD = 0.0021


def _ingestion_cost(pages: int, scanned: bool) -> float:
    rate = COST_PER_PAGE_OCR_USD if scanned else COST_PER_PAGE_PARSE_USD
    return round(pages * rate, 6)


def _index_detail(outcome: IndexOutcome, span_count: int) -> str:
    """The step-log line for step 6. Says what actually happened, including the
    case where nothing was embedded, because "Embedded & indexed · 0 vectors"
    reads as success to anyone not counting."""
    if not outcome.vectors_written:
        reason = outcome.warnings[0] if outcome.warnings else "no vector store configured"
        return f"{span_count} resolvable spans · not retrievable: {reason}"
    saved = outcome.written - outcome.embedded
    return (
        f"{outcome.index_version} · {outcome.written} points "
        f"({outcome.children} chunks + {outcome.parents} parents) · "
        f"{outcome.embedded} embedded"
        + (f", {saved} reused" if saved > 0 else "")
        + f" in {outcome.embed_ms / 1000:.1f}s · {span_count} resolvable spans"
    )


def _pages_needing_ocr(result: ParseResult, floor: int) -> list[int]:
    """Which pages of an already-scanned document to recognise.

    Called only once the document-level gate has fired, so this is not the
    branch decision — it is what to do inside the branch. A part-scanned PDF
    keeps the pages that had a usable text layer, because recognition is
    strictly worse than real extracted text and replacing it would lose
    character offsets the citations already resolve against.

    The floor is the same one that decided the document-level branch, applied
    page by page rather than to the mean.
    """
    return [p.page_no for p in result.pages if len(p.text) < floor]


def _run_ocr(
    ocr: Any, source: Path, document: Document, pages: list[int], total_pages: int
) -> ParseResult:
    """OCR, with the one precondition the engines cannot check for themselves.

    The rasteriser is PDF-only. A DOCX or TXT that reached here has no text to
    recognise *and* no pages to render, which means the file is empty or
    corrupt rather than scanned — a different failure with a different next
    action, so it is named rather than funnelled through OCR.
    """
    if document.ext != ".pdf":
        raise IngestionFailure(
            reason="The document contained no readable text",
            detail=(
                f"No text could be extracted from this {document.ext} file, and OCR only "
                "applies to scanned PDFs. The file may be empty or corrupt — try "
                "re-saving it, or upload a PDF."
            ),
        )
    return ocr.run(str(source), pages, total_pages=total_pages)


def _quarantine_reason(result: ParseResult, qa: object) -> str:
    failed_pages = sum(1 for p in result.pages if len(p.text) < 40)
    if failed_pages and result.page_count:
        return f"Text extraction failed on {failed_pages} of {result.page_count} pages"
    return "The document did not pass the layout quality gate"


def _parse(source: Path, document: Document, max_pages: int) -> ParseResult:
    parser = parser_for(document.ext)
    if parser is None:
        raise IngestionFailure(
            reason=f"No parser for {document.ext}",
            detail="This file type is not supported. Legal Lens accepts PDF, DOCX and TXT.",
        )
    try:
        return parser.parse(str(source), max_pages=max_pages)
    except ValueError as exc:
        raise IngestionFailure(reason="The document could not be read", detail=str(exc)) from exc


def _merge_ocr(base: ParseResult, ocr: ParseResult) -> ParseResult:
    """OCR output replaces only the pages it covered, so a part-scanned PDF
    keeps its machine-readable pages.

    A page the engine returned empty keeps whatever the text layer had: OCR
    failing on one page should not also discard the characters pypdf did find
    there, thin as they were.
    """
    by_page = {p.page_no: p for p in base.pages}
    for page in ocr.pages:
        existing = by_page.get(page.page_no)
        by_page[page.page_no] = ParsedPage(
            page_no=page.page_no,
            text=page.text or (existing.text if existing else ""),
            ocr_confidence=page.ocr_confidence,
            engine=page.engine if page.text else (existing.engine if existing else page.engine),
        )
    return ParseResult(
        pages=[by_page[k] for k in sorted(by_page)],
        scanned=True,
        engine=f"{base.engine}+{ocr.engine}",
        warnings=[*base.warnings, *ocr.warnings],
    )


def _mean_confidence(pages: list[ParsedPage]) -> float:
    scored = [p.ocr_confidence for p in pages if p.ocr_confidence is not None]
    return sum(scored) / len(scored) if scored else 1.0


def _decrypt_to_temp(document: Document) -> Path:
    """The pipeline needs a file on disk; the stored blob is encrypted. The
    plaintext lives in the data dir for the duration of the job and is unlinked
    in a `finally`, so a crashed worker leaves at most one file behind — which
    the next startup sweeps."""
    if not document.blob_key:
        raise IngestionFailure(
            reason="The uploaded file is missing",
            detail="The stored file could not be located. Upload it again.",
        )
    data = blob.read(document.blob_key)
    tmp_dir = get_settings().data_dir / "work"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / f"{document.id}{document.ext}"
    path.write_bytes(data)
    return path


async def _persist_page_images(
    document: Document, result: ParseResult, source: Path, settings: Any
) -> dict[int, str]:
    """Render and store one encrypted PNG per page, for the viewer.

    Scanned pages only by default (`LL_PAGE_IMAGES=scanned`). A PDF with a text
    layer is rendered client-side by PDF.js, so rasterising it here would cost
    1-2 MB of ciphertext a page to duplicate something the browser already does.
    A scanned page has nothing to render as text, so the image *is* the document.

    Failure is non-fatal and returns what it managed: the images are a viewing
    aid, and refusing to index a readable document because a bitmap would not
    encode would be the wrong trade.
    """
    mode = settings.page_images
    if mode == "off" or document.ext != ".pdf":
        return {}
    if mode == "scanned" and not result.scanned:
        return {}

    wanted = (
        [p.page_no for p in result.pages if p.ocr_confidence is not None]
        if mode == "scanned"
        else [p.page_no for p in result.pages]
    )
    if not wanted:
        return {}

    try:
        return await asyncio.to_thread(
            _render_and_store, str(source), document.id, wanted, settings.ocr_dpi
        )
    except Exception as exc:  # noqa: BLE001 — a viewing aid must not fail ingestion
        log.warning("page images for %s were not stored: %s", document.id, exc)
        return {}


def _render_and_store(path: str, document_id: str, pages: list[int], dpi: int) -> dict[int, str]:
    from app.ingestion.ocr import render_pages

    stored: dict[int, str] = {}
    for image in render_pages(path, pages, dpi=dpi):
        key = blob.object_key("images", document_id, f"p{image.page_no}")
        blob.write(key, image.data)
        stored[image.page_no] = key
    return stored


async def _persist_pages(
    db: AsyncSession,
    document: Document,
    pages: list[ParsedPage],
    images: dict[int, str] | None = None,
) -> None:
    """Page text is encrypted at rest — it is as privileged as the PDF (DR-5)."""
    images = images or {}
    existing = await db.execute(select(DocumentPage).where(DocumentPage.document_id == document.id))
    for row in existing.scalars():
        if row.text_key:
            await asyncio.to_thread(blob.delete, row.text_key)
        if row.image_key and row.image_key not in images.values():
            await asyncio.to_thread(blob.delete, row.image_key)
        await db.delete(row)
    await db.flush()

    doc_offset = 0
    for page in pages:
        key = blob.object_key("pages", document.id, f"p{page.page_no}")
        await asyncio.to_thread(blob.write_text, key, page.text)
        db.add(
            DocumentPage(
                document_id=document.id,
                page_no=page.page_no,
                char_count=len(page.text),
                doc_char_start=doc_offset,
                ocr_confidence=page.ocr_confidence,
                text_key=key,
                image_key=images.get(page.page_no),
                parse_engine=page.engine,
            )
        )
        doc_offset += len(page.text) + 1
    await db.flush()


async def _persist_spans(db: AsyncSession, document: Document, chunks: list[Chunk]) -> int:
    """One span per child chunk. Offsets are page-local to `page_from`, which is
    the unit the viewer can highlight (BACKEND-TODO §8.2)."""
    existing = await db.execute(select(DocumentSpan).where(DocumentSpan.document_id == document.id))
    for row in existing.scalars():
        await db.delete(row)
    await db.flush()

    count = 0
    for chunk in chunks:
        if chunk.is_parent:
            continue
        db.add(
            DocumentSpan(
                id=prefixed_id("s"),
                document_id=document.id,
                page_no=chunk.page_from,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                section=chunk.section_path,
            )
        )
        count += 1
    await db.flush()
    return count


async def _persist_chunks(
    db: AsyncSession, document: Document, chunks: list[Chunk], index_version: str
) -> list[ChunkMeta]:
    """Write `chunk_meta` and return the rows **aligned to `chunks`**.

    The alignment is the point. `IndexOutcome.point_ids` comes back in the order
    the chunks were submitted, so returning a list in that same order is what
    lets `vector_point_id` be filled in without matching rows up by offsets
    afterwards — a match that would be ambiguous for a parent and its only
    child, which share a page and a character range.
    """
    existing = await db.execute(select(ChunkMeta).where(ChunkMeta.document_id == document.id))
    for row in existing.scalars():
        await db.delete(row)
    await db.flush()

    rows: list[ChunkMeta | None] = [None] * len(chunks)
    parent_ids: dict[str, str] = {}

    # Parents first: a child's `parent_chunk_id` is a real foreign key into this
    # same table, so the parent row has to have an id before the child is built.
    for position, chunk in enumerate(chunks):
        if not chunk.is_parent:
            continue
        row = ChunkMeta(
            document_id=document.id,
            page_from=chunk.page_from,
            page_to=chunk.page_to,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            section_path=chunk.section_path,
            clause_no=chunk.clause_no,
            token_count=chunk.token_count,
            index_version=index_version,
        )
        db.add(row)
        await db.flush()
        rows[position] = row
        if chunk.parent_key:
            parent_ids[chunk.parent_key] = row.id

    for position, chunk in enumerate(chunks):
        if chunk.is_parent:
            continue
        row = ChunkMeta(
            document_id=document.id,
            parent_chunk_id=parent_ids.get(chunk.parent_key or ""),
            page_from=chunk.page_from,
            page_to=chunk.page_to,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            section_path=chunk.section_path,
            clause_no=chunk.clause_no,
            token_count=chunk.token_count,
            index_version=index_version,
        )
        db.add(row)
        rows[position] = row
    await db.flush()

    if any(r is None for r in rows):
        # Both loops above cover every chunk, so this cannot happen without a
        # code change. Saying so is cheaper than the alternative, which is a
        # silently shortened list that misaligns every point id after the gap.
        raise RuntimeError(
            "chunk_meta rows are not aligned to the chunk list; "
            "vector point ids would be attached to the wrong clauses"
        )
    return [r for r in rows if r is not None]


async def _register_index_version(db: AsyncSession, outcome: IndexOutcome) -> None:
    """NFR-12: an embedding-model or chunker change becomes a new index version.

    Written here, on the first document ingested under a version, rather than at
    startup or by a migration. The reason is that the version string is composed
    from what actually ran — the model the embedder loaded and the chunker that
    produced the boundaries — so the only moment it is known to be true is just
    after a document was indexed with it.

    The rollback story needs the row to exist before it needs anything else in
    it: given a stale version, `chunk_meta.index_version` finds every row to
    rewrite, and this table says what that version *was*.
    """
    found = (
        await db.execute(select(IndexVersion).where(IndexVersion.name == outcome.index_version))
    ).scalar_one_or_none()
    if found is not None:
        return
    db.add(
        IndexVersion(
            name=outcome.index_version[:64],
            embedding_model=outcome.embedding_model,
            chunker=outcome.chunker,
            # Reranking is a read-path concern and ingestion does not know which
            # reranker will score these chunks. Left NULL rather than guessed.
            reranker=None,
            active=1,
        )
    )
    await db.flush()


async def _record_point_ids(db: AsyncSession, rows: list[ChunkMeta], outcome: IndexOutcome) -> None:
    """Fill `chunk_meta.vector_point_id` from what the store actually wrote.

    NFR-12's re-index story needs the join to exist in both directions: given a
    row, find its vector; given a stale index version, find every row to rewrite.
    A length mismatch is logged rather than raised — the vectors are already in
    the store at this point, so failing the document here would leave the
    collection ahead of the database, which is the worse of the two
    inconsistencies.
    """
    if not outcome.point_ids:
        return
    if len(outcome.point_ids) != len(rows):
        log.warning(
            "indexer returned %d point ids for %d chunk rows; vector_point_id left unset",
            len(outcome.point_ids),
            len(rows),
        )
        return
    for row, point_id in zip(rows, outcome.point_ids, strict=True):
        row.vector_point_id = point_id
    await db.flush()
