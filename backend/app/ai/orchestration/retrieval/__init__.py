"""Hybrid retrieval: bge-m3 dense+sparse into Qdrant, bge-reranker-v2-m3 on top.

Both halves live here. `pipeline.py` reads — embed the query, hybrid search,
rerank, expand to parents — and `writer.py` writes, which is the step the
ingestion pipeline reaches through `app/indexing.py`. They share one store and
one embedder, from `factory.py`, because local-mode Qdrant takes an exclusive
lock on its directory and the models are ~2.2 GB each.

Implementations are imported lazily by `factory.py` so that importing this
package does not pull in torch, and the offline tests never download a model.
"""

from __future__ import annotations

from app.ai.orchestration.retrieval.pipeline import RetrievalResult, Retriever
from app.ai.orchestration.retrieval.ports import (
    Embedder,
    Embedding,
    Hit,
    IndexedChunk,
    Reranker,
    SparseVector,
    VectorStore,
)
from app.ai.orchestration.retrieval.writer import (
    DuplicateParentKey,
    IndexWriter,
    IndexWriteResult,
    point_id_for,
)

__all__ = [
    "DuplicateParentKey",
    "Embedder",
    "Embedding",
    "Hit",
    "IndexWriteResult",
    "IndexWriter",
    "IndexedChunk",
    "RetrievalResult",
    "Reranker",
    "Retriever",
    "SparseVector",
    "VectorStore",
    "point_id_for",
]
