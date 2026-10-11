"""The seams the M4 pipeline replaces.

Each port is a Protocol with a stub implementation in `app/ingestion/stub/` or a
real one in `app/ingestion/extract/`. The split is deliberate and is the §7.1
decision in BACKEND-TODO:

  REAL   text extraction for TXT, DOCX and the PDF text layer, with per-page
         character offsets (`app/ingestion/extract/`); OCR for scanned PDFs
         (`app/ingestion/ocr/`, Tesseract or RapidOCR over a pypdfium2
         rasteriser); section-aware chunking; and embedding + vector upsert,
         wired through `app/indexing.py` to bge-m3 and Qdrant.
  STUB   layout QA and classification. Both are heuristic proxies today and
         both say so in their own modules; the real reading-order, column and
         table checks are BRD §15's expensive half.

Every port keeps its stub implementation, and every real implementation reports
its own availability. That is not symmetry for its own sake: `uv sync` with no
extras must still produce a startable backend, so "the real engine is not
installed" has to be a named, reportable condition rather than an import error
at module load.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class ParsedPage:
    """One page of extracted text. `char_count` is len(text); offsets handed to
    spans are page-local indexes into `text`."""

    page_no: int
    text: str
    ocr_confidence: float | None = None
    engine: str = "unknown"


@dataclass
class ParseResult:
    pages: list[ParsedPage] = field(default_factory=list)
    # True when the source had no usable text layer and OCR was required (DR-1).
    scanned: bool = False
    engine: str = "unknown"
    warnings: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def total_chars(self) -> int:
        return sum(len(p.text) for p in self.pages)


@dataclass
class LayoutQaResult:
    """BRD §15: a clause pulled from a mis-ordered two-column page looks
    correct, which is why it is worse than no extraction. The gate quarantines
    rather than indexing text it does not trust."""

    reading_order_score: float
    column_sanity: bool
    table_integrity: bool
    quarantine: bool = False
    reason: str | None = None
    flagged_pages: list[int] = field(default_factory=list)


@dataclass
class Classification:
    doc_type: str  # contract | opinion | pleading | other
    confidence: float


@dataclass
class Chunk:
    """A child chunk plus the parent section it expands to. Offsets are
    page-local to `page_from`; a chunk spanning pages records both ends."""

    page_from: int
    page_to: int
    char_start: int
    char_end: int
    text: str
    section_path: str | None = None
    clause_no: str | None = None
    token_count: int = 0
    parent_key: str | None = None
    is_parent: bool = False
    #: Set by the pipeline after `chunk_meta` is persisted, so the vector point
    #: and the database row can be matched up afterwards. The indexer derives
    #: its point id from this, which is what makes re-indexing idempotent.
    chunk_id: str | None = None


@dataclass(frozen=True)
class DocumentRef:
    """The document identity an index entry needs.

    The `Indexer` port takes this rather than a bare id because a retrieved
    chunk has to be able to label its own citation — "Acme NDA · § 12.3 · p.4" —
    and the vector store payload is the only place that text exists on the read
    path. Passing the id alone would mean the AI layer needed a database lookup
    to render a citation chip, which NFR-14 forbids.
    """

    id: str
    name: str
    type: str


@dataclass
class IndexOutcome:
    """What indexing did, in enough detail for the step log and the audit row.

    `point_ids` is aligned to the submitted chunk list, including `None` for a
    chunk that was not written, so the caller can store the ids it did get
    without guessing at the alignment.
    """

    index_version: str
    engine: str = "unknown"
    #: The two things a change to which invalidates the index (NFR-12). Carried
    #: on the outcome rather than looked up by the caller, because ingestion
    #: must not import the AI layer to ask what model ran.
    embedding_model: str | None = None
    chunker: str | None = None
    point_ids: list[str | None] = field(default_factory=list)
    children: int = 0
    parents: int = 0
    #: Distinct texts embedded. Lower than `children + parents` whenever a
    #: section fit in one chunk and its parent and child are the same string.
    embedded: int = 0
    embed_ms: float = 0.0
    upsert_ms: float = 0.0
    #: Points removed before the write, i.e. a re-ingestion replacing itself.
    replaced: int = 0
    #: True when real vectors were written. False for the stub, and the
    #: difference is recorded rather than inferred from the index version.
    vectors_written: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def written(self) -> int:
        return self.children + self.parents


@runtime_checkable
class Parser(Protocol):
    """Format-specific text extraction."""

    name: str

    def supports(self, ext: str) -> bool: ...

    def parse(self, path: str, *, max_pages: int) -> ParseResult: ...


@runtime_checkable
class Ocr(Protocol):
    """Recognition for pages with no usable text layer.

    Real implementations live in `app/ingestion/ocr/` (Tesseract and RapidOCR,
    both over a pypdfium2 rasteriser); `app/ingestion/stub/ocr.py` is the
    honest no-op for an install without them.

    `available()` and `unavailable_reason()` are part of the port, not an
    implementation detail. The pipeline needs to distinguish "no OCR engine is
    installed" from "OCR ran and the page was illegible": they have different
    causes, different next actions for the attorney, and only one of them is
    fixed by re-uploading the file.

    An implementation must never invent text. A page it cannot read returns
    empty text and a low or absent confidence, so the layout QA gate
    quarantines the document instead of marking it ready with nothing in it.
    """

    @property
    def name(self) -> str: ...

    def available(self) -> bool: ...

    def unavailable_reason(self) -> str | None: ...

    def run(self, path: str, pages: list[int], *, total_pages: int) -> ParseResult: ...


@runtime_checkable
class LayoutQa(Protocol):
    """STUB in M3. Real reading-order and column checks at M4."""

    name: str

    def check(self, result: ParseResult) -> LayoutQaResult: ...


@runtime_checkable
class Classifier(Protocol):
    """STUB in M3 — heuristics over filename and first-page text. FR-5 only
    requires that a type is shown and overridable, which a heuristic satisfies."""

    name: str

    def classify(self, filename: str, pages: list[ParsedPage]) -> Classification: ...


@runtime_checkable
class Chunker(Protocol):
    """Section-aware chunking. Real in M3 because `chunk_meta` rows and span
    offsets are backend data, not model data."""

    name: str

    def chunk(self, pages: list[ParsedPage], doc_type: str) -> list[Chunk]: ...


@runtime_checkable
class Indexer(Protocol):
    """Embedding and vector upsert, behind one seam.

    The real implementation is wired in `app/indexing.py` — outside this
    package on purpose. `tests/test_layering.py` forbids `app/ingestion` from
    importing `app.ai`, and that rule is worth keeping: ingestion should depend
    on the *shape* of indexing, which is this protocol, and not on bge-m3 or
    Qdrant. `app/indexing.py` is the one place that knows both, exactly as
    `app/ai/engine.py` is the one place that constructs an engine.

    `index` takes **every** chunk, parents included. Parent sections are not
    vector-searched, but `Retriever._expand` fetches them by `parent_key`, so a
    collection holding children alone turns parent-document retrieval into
    naive top-K without raising anything.
    """

    #: Read-only on purpose. The real indexer composes `index_version` from the
    #: embedding model and the chunker rather than storing it, so that a model
    #: swap cannot leave `chunk_meta` rows claiming to belong to an index that
    #: no longer exists (NFR-12). A settable attribute would permit exactly that.
    @property
    def name(self) -> str: ...

    @property
    def index_version(self) -> str: ...

    def index(self, document: DocumentRef, chunks: list[Chunk]) -> IndexOutcome: ...

    def delete_document(self, document_id: str) -> int: ...
