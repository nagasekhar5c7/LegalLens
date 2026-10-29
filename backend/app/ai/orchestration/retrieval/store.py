"""Qdrant, holding dense and sparse vectors in one collection.

Local embedded mode (`QdrantClient(path=...)`) so development needs no Docker;
the same client talks to a server at M6 by passing `url` instead. One caveat
worth knowing before debugging a lock error: **local mode takes an exclusive
lock on the directory**, so the indexing script and a running engine cannot
share one path at the same time.

Why both vector kinds live in one collection: Qdrant's Query API takes two
`prefetch` branches — dense and sparse — and fuses them server-side with RRF.
That is the entire hybrid search, with no second index, no client-side score
normalisation, and no BM25 cache to invalidate when a document is uploaded.

The payload carries chunk text. That is what keeps the AI layer free of any
database import (NFR-14, and `tests/test_layering.py` enforces it), and it is
also a trade: Qdrant has no payload encryption, so this directory holds
plaintext clause text and needs filesystem-level encryption to satisfy NFR-9.
That belongs in the governance note, not in a comment, but it is recorded here
too so it is not discovered by surprise.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from app.ai.orchestration.retrieval.ports import Embedding, Hit, IndexedChunk

log = logging.getLogger("legallens.ai.store")

COLLECTION = "legallens_chunks"
DENSE_VECTOR = "dense"
SPARSE_VECTOR = "sparse"

#: Rank-based fusion constant. Qdrant's own default; kept explicit because RRF
#: output is meaningless as an absolute score and the refusal floor must never
#: be applied to it — only to the reranker's normalised score.
_RRF_NOTE = "scores from RRF are ranks, not relevance — threshold after rerank"


class QdrantStore:
    """Hybrid search over child and parent chunks."""

    name = "qdrant/local"

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        url: str | None = None,
        collection: str = COLLECTION,
        api_key: str | None = None,
    ) -> None:
        from qdrant_client import QdrantClient

        self.collection = collection
        self._local = not url
        if url:
            self.client = QdrantClient(url=url, api_key=api_key)
            self.name = f"qdrant/{url}"
        else:
            target = Path(path or "data/qdrant")
            target.parent.mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=str(target))
            self.name = f"qdrant/local:{target}"

    # ---------------------------------------------------------- collection

    def ensure_collection(self, *, dim: int) -> None:
        from qdrant_client import models

        if self.client.collection_exists(self.collection):
            return
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config={
                DENSE_VECTOR: models.VectorParams(size=dim, distance=models.Distance.COSINE)
            },
            sparse_vectors_config={SPARSE_VECTOR: models.SparseVectorParams()},
        )
        # FR-7/FR-9 filter by document on every query, and FR-26 deletes by it.
        # Without these the filter degrades to a full scan as the corpus grows.
        #
        # Skipped in local mode, where Qdrant has no payload indexes and warns
        # "Payload indexes have no effect in the local Qdrant" on every start.
        # The filters still apply — local mode scans — so this costs nothing at
        # NFR-5 scale and the indexes are created when a server is configured.
        if self._local:
            log.info("local Qdrant: payload indexes skipped (no effect); filters still apply")
            log.info("created collection %s (dim=%d)", self.collection, dim)
            return
        for field in ("document_id", "parent_key", "is_parent"):
            try:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD
                    if field != "is_parent"
                    else models.PayloadSchemaType.BOOL,
                )
            except Exception as exc:  # noqa: BLE001 — index already present
                log.debug("payload index %s: %s", field, exc)
        log.info("created collection %s (dim=%d)", self.collection, dim)

    def count(self) -> int:
        if not self.client.collection_exists(self.collection):
            return 0
        return int(self.client.count(self.collection, exact=True).count)

    # -------------------------------------------------------------- upsert

    def upsert(self, chunks: list[IndexedChunk], embeddings: list[Embedding]) -> int:
        from qdrant_client import models

        if not chunks:
            return 0
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"{len(chunks)} chunks but {len(embeddings)} embeddings — "
                "a mismatch here would attach a vector to the wrong clause"
            )

        points: list[Any] = []
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            vectors: dict[str, Any] = {DENSE_VECTOR: list(embedding.dense)}
            if embedding.sparse:
                vectors[SPARSE_VECTOR] = models.SparseVector(
                    indices=list(embedding.sparse.indices),
                    values=list(embedding.sparse.values),
                )
            points.append(
                models.PointStruct(
                    # Deterministic, so re-indexing a document replaces its
                    # points instead of duplicating them.
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id)),
                    vector=vectors,
                    payload=_to_payload(chunk),
                )
            )

        self.client.upsert(collection_name=self.collection, points=points, wait=True)
        return len(points)

    # -------------------------------------------------------------- search

    def search(self, embedding: Embedding, *, document_ids: list[str], top_k: int) -> list[Hit]:
        """Dense + sparse prefetch, fused server-side by RRF.

        Only child chunks are searched. Parents exist in the collection so
        expansion can fetch them, but retrieving a 3,000-token parent directly
        would hand the reranker a passage it cannot score in one window.
        """
        from qdrant_client import models

        if not document_ids:
            # FR-7: empty scope means nothing is retrievable. Returning early
            # rather than querying unfiltered is the safe direction to fail.
            return []

        scope = models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id", match=models.MatchAny(any=list(document_ids))
                ),
                models.FieldCondition(key="is_parent", match=models.MatchValue(value=False)),
            ]
        )

        # Over-fetch each branch so fusion has something to fuse: taking top_k
        # from each and fusing to top_k would make RRF nearly a no-op.
        branch_limit = max(top_k * 2, top_k + 10)
        prefetch = [
            models.Prefetch(
                query=list(embedding.dense),
                using=DENSE_VECTOR,
                filter=scope,
                limit=branch_limit,
            )
        ]
        if embedding.sparse:
            prefetch.append(
                models.Prefetch(
                    query=models.SparseVector(
                        indices=list(embedding.sparse.indices),
                        values=list(embedding.sparse.values),
                    ),
                    using=SPARSE_VECTOR,
                    filter=scope,
                    limit=branch_limit,
                )
            )

        response = self.client.query_points(
            collection_name=self.collection,
            prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=scope,
            limit=top_k,
            with_payload=True,
        )
        return [
            Hit(chunk=_from_payload(p.payload or {}), score=float(p.score), source="fused")
            for p in response.points
        ]

    # -------------------------------------------------------------- parents

    def fetch_parents(
        self, parent_keys: list[str], *, document_ids: list[str]
    ) -> list[IndexedChunk]:
        """The expansion step. Scoped as tightly as search, because a parent is
        document text too and FR-7 does not stop applying after retrieval."""
        from qdrant_client import models

        if not parent_keys or not document_ids:
            return []
        found, _ = self.client.scroll(
            collection_name=self.collection,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="document_id", match=models.MatchAny(any=list(document_ids))
                    ),
                    models.FieldCondition(
                        key="parent_key", match=models.MatchAny(any=list(parent_keys))
                    ),
                    models.FieldCondition(key="is_parent", match=models.MatchValue(value=True)),
                ]
            ),
            limit=len(parent_keys) * 2 + 8,
            with_payload=True,
        )
        return [_from_payload(p.payload or {}) for p in found]

    # --------------------------------------------------------------- delete

    def delete_document(self, document_id: str) -> int:
        """FR-26: a filtered delete, effective immediately for retrieval.

        The count is read first so the caller can log what actually went,
        rather than reporting success for a no-op.
        """
        from qdrant_client import models

        if not self.client.collection_exists(self.collection):
            return 0
        selector = models.Filter(
            must=[
                models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))
            ]
        )
        before = int(self.client.count(self.collection, count_filter=selector, exact=True).count)
        if before:
            self.client.delete(
                collection_name=self.collection,
                points_selector=models.FilterSelector(filter=selector),
                wait=True,
            )
        return before


def _to_payload(chunk: IndexedChunk) -> dict[str, Any]:
    return {
        "chunk_id": chunk.chunk_id,
        "document_id": chunk.document_id,
        "document_name": chunk.document_name,
        "document_type": chunk.document_type,
        "page_from": chunk.page_from,
        "page_to": chunk.page_to,
        "char_start": chunk.char_start,
        "char_end": chunk.char_end,
        "text": chunk.text,
        "section_path": chunk.section_path,
        "clause_no": chunk.clause_no,
        "parent_key": chunk.parent_key,
        "is_parent": chunk.is_parent,
    }


def _from_payload(payload: dict[str, Any]) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=str(payload.get("chunk_id", "")),
        document_id=str(payload.get("document_id", "")),
        document_name=str(payload.get("document_name", "")),
        document_type=str(payload.get("document_type", "other")),
        page_from=int(payload.get("page_from", 1)),
        page_to=int(payload.get("page_to", 1)),
        char_start=int(payload.get("char_start", 0)),
        char_end=int(payload.get("char_end", 0)),
        text=str(payload.get("text", "")),
        section_path=payload.get("section_path"),
        clause_no=payload.get("clause_no"),
        parent_key=payload.get("parent_key"),
        is_parent=bool(payload.get("is_parent", False)),
    )
