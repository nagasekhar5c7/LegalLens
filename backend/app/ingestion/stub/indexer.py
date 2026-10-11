"""The no-vector-store fallback.

The real indexer is wired in `app/indexing.py`: bge-m3 embeddings upserted into
Qdrant. This stub is what `LL_INDEXER=stub` selects, and what `auto` resolves to
when `qdrant-client` or `FlagEmbedding` is not installed.

It stamps an `index_version` and returns no point ids, so `chunk_meta` rows
exist with `vector_point_id = NULL`. That is the state the whole of M3 ran in,
and it is still the right state for a checkout that has not installed the
retrieval extra: the documents parse, the pages and spans are real, and
retrieval returns nothing rather than pretending.

`delete_document` reports 0 rather than silently succeeding, so the FR-26
deletion report says what actually happened instead of claiming a no-op worked.
"""

from __future__ import annotations

import logging

from app.ingestion.ports import Chunk, DocumentRef, IndexOutcome

log = logging.getLogger("legallens.ingest.index")


class StubIndexer:
    name = "indexer/none"
    index_version = "no-vectors-v1"

    def available(self) -> bool:
        return False

    def unavailable_reason(self) -> str:
        return (
            "no vector store is configured. Install the retrieval extra "
            "(`uv sync --extra retrieval`) and set LL_INDEXER=qdrant"
        )

    def index(self, document: DocumentRef, chunks: list[Chunk]) -> IndexOutcome:
        parents = sum(1 for c in chunks if c.is_parent)
        log.info(
            "indexed %d chunks for %s into %s (no vectors — %s)",
            len(chunks),
            document.id,
            self.index_version,
            self.unavailable_reason(),
        )
        return IndexOutcome(
            index_version=self.index_version,
            engine=self.name,
            point_ids=[None] * len(chunks),
            children=len(chunks) - parents,
            parents=parents,
            embedded=0,
            vectors_written=False,
            warnings=[f"nothing was embedded: {self.unavailable_reason()}"],
        )

    def delete_document(self, document_id: str) -> int:
        """No vector store, so nothing to delete — reported as 0 rather than
        silently succeeding, so the delete log says what actually happened."""
        return 0
