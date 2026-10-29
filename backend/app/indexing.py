"""The one place that knows both ingestion and retrieval.

`app/ai/engine.py` is the single wiring point for the AI engine. This is the
same idea for the index, and it exists for the same reason, which
`tests/test_layering.py` enforces from two directions:

  * `app/ingestion/**` must not import `app.ai`. Ingestion owns the `Indexer`
    protocol and should depend on the *shape* of indexing, not on bge-m3 or
    Qdrant. A pipeline that imported the AI layer would make parsing a document
    depend on how answers get written.
  * `app/ai/**` must not import `app.models`, `app.db` or `sqlalchemy`. An AI
    layer that could read the database could also retrieve a document the scope
    resolver excluded, which is FR-7 defeated from the inside.

Those two rules leave no legal home for the adapter inside either package, so it
lives here, at the app root, importing both deliberately. This module is the
only file in the repository that does.

Four translations happen here, and each one prevents a specific, quiet failure.

**Chunk ids are deterministic and document-scoped.** `chunk_meta.id` is a fresh
`ch_…` on every re-ingestion, so using it as the vector key would orphan the
previous points. The id below is derived from the document, the page and the
character range, so re-ingesting an unchanged document overwrites its own points
instead of accumulating ghosts beside them.

**`parent_key` is namespaced by document.** The chunker emits `p0`, `p1`, … per
document. `store.fetch_parents` filters by `parent_key` across *every* document
in scope, so two documents in one workspace both offering `p0` would let one
document's child expand into the other document's section — a citation
pointing at a clause the attorney never asked about. `IndexWriter` refuses a
batch whose keys are not namespaced; this is where they get namespaced.

**Parents go in with the children.** `Retriever._expand` fetches parents from
the store, and a child whose parent is missing silently falls back to citing
itself. Nothing errors; the answers just get narrower. So `index()` receives
every chunk and the `is_parent` flag does the separating.

**Availability is reported, not assumed.** `uv sync` with no extras has to
produce a startable backend, and that backend has no vector store. `LL_INDEXER=auto`
therefore resolves to the stub when the retrieval extra is missing — and says
which, in the step log, in the audit row and on the readiness endpoint, because
a silent fallback is how a demo works on one machine and indexes nothing on
another.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from app.config import get_settings
from app.ingestion.ports import Chunk, DocumentRef, Indexer, IndexOutcome

if TYPE_CHECKING:  # pragma: no cover
    from app.ai.orchestration.retrieval.ports import IndexedChunk

log = logging.getLogger("legallens.indexing")

INDEXERS = ("auto", "qdrant", "stub")

#: Bumped when anything that changes a stored vector changes: the embedding
#: model, the chunker's boundaries, or the payload shape. NFR-12's rollback
#: story is only real if a stale index can be recognised as stale, and a
#: version string that never moves cannot do that.
INDEX_SCHEMA = "v1"


def chunk_key(document_id: str, chunk: Chunk) -> str:
    """The stable vector key for one chunk.

    Deliberately derived rather than stored. A parent and its single child can
    carry byte-identical text and the same offsets, so `is_parent` is part of
    the key — without it the two would collide and one would overwrite the
    other, costing the collection every parent of a short section.
    """
    kind = "P" if chunk.is_parent else "C"
    return (
        f"{document_id}:{kind}:{chunk.page_from}-{chunk.page_to}"
        f":{chunk.char_start}-{chunk.char_end}"
    )


def parent_key(document_id: str, key: str | None) -> str | None:
    return f"{document_id}:{key}" if key else None


class QdrantChunkIndexer:
    """`Indexer` over the real retrieval stack: bge-m3 embeddings into Qdrant.

    Holds no model or client of its own — both come from
    `retrieval.factory`, which caches them per process. That is not tidiness:
    local-mode Qdrant takes an exclusive lock on its directory, so an indexer
    with a private client would fight the running retriever for it.
    """

    name = "indexer/bge-m3+qdrant"

    def __init__(self, *, batch_size: int | None = None) -> None:
        from app.ai.orchestration.retrieval.writer import EMBED_BATCH

        self._batch_size = batch_size or EMBED_BATCH
        self._writer: Any | None = None
        self._lock = threading.Lock()

    @property
    def index_version(self) -> str:
        """Stamped on every `chunk_meta` row and every audit row.

        Composed from the embedding model and the chunker rather than
        hand-written, so a model swap cannot leave rows claiming to belong to an
        index that no longer exists.
        """
        from app.ai.orchestration.settings import get_router_settings
        from app.ingestion.stub.chunker import SectionAwareChunker

        model = get_router_settings().embed_model.rsplit("/", 1)[-1]
        chunker = SectionAwareChunker.name.split("/", 1)[-1]
        return f"{model}+{chunker}+qdrant:{INDEX_SCHEMA}"

    def available(self) -> bool:
        return not self.unavailable_reason()

    def unavailable_reason(self) -> str | None:
        from importlib.util import find_spec

        missing = [
            name
            for name, spec in (
                ("qdrant-client", "qdrant_client"),
                ("FlagEmbedding", "FlagEmbedding"),
            )
            if find_spec(spec) is None
        ]
        if missing:
            return f"{', '.join(missing)} not installed (uv sync --extra retrieval)"
        return None

    def _get_writer(self) -> Any:
        if self._writer is not None:
            return self._writer
        with self._lock:
            if self._writer is None:
                from app.ai.orchestration.retrieval import factory
                from app.ai.orchestration.retrieval.writer import IndexWriter

                self._writer = IndexWriter(
                    store=factory.get_store(),
                    embedder=factory.get_embedder(),
                    batch_size=self._batch_size,
                )
            return self._writer

    # ------------------------------------------------------------- the port

    def index(self, document: DocumentRef, chunks: list[Chunk]) -> IndexOutcome:
        reason = self.unavailable_reason()
        if reason is not None:
            # Deliberately not a raise: the attorney's document parsed fine and
            # its pages and spans are real. What failed is retrieval, and the
            # honest outcome is a document that is readable but not searchable,
            # with the reason recorded — not a failed ingestion.
            return IndexOutcome(
                index_version=self.index_version,
                engine=self.name,
                point_ids=[None] * len(chunks),
                children=sum(1 for c in chunks if not c.is_parent),
                parents=sum(1 for c in chunks if c.is_parent),
                vectors_written=False,
                warnings=[f"nothing was embedded: {reason}"],
            )

        writer = self._get_writer()
        indexed = [to_indexed_chunk(document, c) for c in chunks]
        written = writer.write(document.id, indexed)

        return IndexOutcome(
            index_version=self.index_version,
            engine=writer.name,
            embedding_model=self._embedding_model(),
            chunker=self._chunker(),
            point_ids=written.point_ids,
            children=written.children,
            parents=written.parents,
            embedded=written.embedded,
            embed_ms=written.embed_ms,
            upsert_ms=written.upsert_ms,
            replaced=written.deleted_stale,
            vectors_written=written.written > 0,
            warnings=written.warnings,
        )

    @staticmethod
    def _embedding_model() -> str:
        from app.ai.orchestration.settings import get_router_settings

        return get_router_settings().embed_model

    @staticmethod
    def _chunker() -> str:
        from app.ingestion.stub.chunker import SectionAwareChunker

        return SectionAwareChunker.name

    def delete_document(self, document_id: str) -> int:
        """FR-26. Returns 0 without constructing anything when the extra is not
        installed — there are no points to remove, and opening a vector store in
        order to delete nothing would be a strange way to fail a delete."""
        if self.unavailable_reason() is not None:
            return 0
        from app.ai.orchestration.retrieval import factory

        return factory.get_store().delete_document(document_id)


def to_indexed_chunk(document: DocumentRef, chunk: Chunk) -> IndexedChunk:
    """`ingestion.ports.Chunk` -> `retrieval.ports.IndexedChunk`.

    The document name and type travel into the payload because a retrieved
    chunk has to label its own citation and the AI layer cannot query the
    database to find out what document it came from (NFR-14).
    """
    from app.ai.orchestration.retrieval.ports import IndexedChunk

    return IndexedChunk(
        chunk_id=chunk.chunk_id or chunk_key(document.id, chunk),
        document_id=document.id,
        document_name=document.name,
        document_type=document.type,
        page_from=chunk.page_from,
        page_to=chunk.page_to,
        char_start=chunk.char_start,
        char_end=chunk.char_end,
        text=chunk.text,
        section_path=chunk.section_path,
        clause_no=chunk.clause_no,
        parent_key=parent_key(document.id, chunk.parent_key),
        is_parent=chunk.is_parent,
    )


# ------------------------------------------------------------------ selection

_indexer: Indexer | None = None
_indexer_lock = threading.Lock()


def build_indexer(choice: str | None = None) -> Indexer:
    """Resolve `LL_INDEXER` to an indexer instance.

    An unknown name raises rather than degrading to the stub: a typo in
    configuration that silently stops indexing every upload is the kind of
    failure that gets found weeks later by an attorney whose search returns
    nothing.
    """
    from app.ingestion.stub.indexer import StubIndexer

    settings = get_settings()
    name = (choice or settings.indexer).strip().lower()
    if name not in INDEXERS:
        raise ValueError(f"LL_INDEXER must be one of {INDEXERS}, not {name!r}")

    if name == "stub":
        return StubIndexer()

    real = QdrantChunkIndexer(batch_size=settings.embed_batch_size)
    if name == "qdrant":
        # Returned even when unavailable, so the reason lands on the document
        # rather than being swallowed by a fallback the operator did not ask for.
        return real
    if real.available():
        return real

    stub = StubIndexer()
    log.warning(
        "no vector store available, falling back to %s — documents will parse but not be "
        "retrievable (%s)",
        stub.name,
        real.unavailable_reason(),
    )
    return stub


def get_indexer() -> Indexer:
    """The process-wide indexer. Cached, because the real one owns a Qdrant lock."""
    global _indexer
    if _indexer is not None:
        return _indexer
    with _indexer_lock:
        if _indexer is None:
            _indexer = build_indexer()
        return _indexer


def reset_indexer() -> None:
    """Drop the cached indexer and release the vector store. For tests and for a
    configuration change in a long-lived process."""
    global _indexer
    with _indexer_lock:
        _indexer = None
    try:
        from app.ai.orchestration.retrieval import factory
    except ImportError:  # pragma: no cover — retrieval extra not installed
        return
    factory.reset()


def describe() -> dict[str, object]:
    """What the readiness endpoint reports about indexing."""
    settings = get_settings()
    real = QdrantChunkIndexer()
    resolved = get_indexer()
    return {
        "configured": settings.indexer,
        "resolved": resolved.name,
        "indexVersion": resolved.index_version,
        "vectors": real.unavailable_reason() or "available",
    }
