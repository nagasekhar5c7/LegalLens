"""The retrieval seams. Protocols first, implementations behind them.

Two rules shape this file.

**No database.** `tests/test_layering.py` forbids the AI layer from importing
`app.models`, `app.db` or `sqlalchemy`, and that rule is load-bearing: an engine
that could read the database could also retrieve a document the scope resolver
excluded, which is FR-7 defeated from the inside. So chunk text travels in the
vector store's payload, not in a database row, and `IndexedChunk` carries
everything retrieval, reranking, assembly and the citation gate need.

**Protocols, because the pieces get swapped.** bge-m3 and bge-reranker-v2-m3 are
today's choice, not a permanent one — a lighter pair (gte-modernbert-base plus
bge-reranker-base) is a drop-in if CPU latency bites. Tests inject fakes and
never download a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

#: Where a hit came from. Kept on the hit so a retrieval failure is diagnosable
#: from the trace without re-running the query (NFR-11).
HitSource = Literal["dense", "sparse", "fused", "reranked", "parent"]


@dataclass(frozen=True)
class IndexedChunk:
    """One retrievable unit, plus everything needed to cite it.

    Offsets are page-local to `page_from`, matching how `spans` are stored —
    the viewer renders page-at-a-time, so that is the unit it can highlight
    (FR-13).

    `text` lives here rather than being re-read from the page blob because the
    AI layer has no blob access either. That is a deliberate trade recorded in
    the plan: the vector store's payload holds plaintext clause text, so its
    data directory needs filesystem-level encryption to satisfy NFR-9. Qdrant
    has no payload encryption of its own.
    """

    chunk_id: str
    document_id: str
    document_name: str
    document_type: str
    page_from: int
    page_to: int
    char_start: int
    char_end: int
    text: str
    section_path: str | None = None
    clause_no: str | None = None
    #: Links a child to the parent section it expands to.
    parent_key: str | None = None
    is_parent: bool = False

    @property
    def span_id(self) -> str:
        """The id a citation resolves to.

        Derived rather than stored so a chunk and its span cannot disagree.
        Page-local offsets make it unique within a document.
        """
        return f"{self.document_id}:{self.page_from}:{self.char_start}-{self.char_end}"

    @property
    def citation_label(self) -> str:
        """What the UI shows on the chip: section if the document has one,
        page otherwise."""
        if self.section_path:
            return f"{self.document_name} · {self.section_path} · p.{self.page_from}"
        return f"{self.document_name} · p.{self.page_from}"


@dataclass(frozen=True)
class SparseVector:
    """bge-m3's learned lexical weights.

    This is why Option A was chosen: the sparse head replaces BM25, so there is
    no second index to build, cache or invalidate. Legal text leans hard on
    exact terms — `§ 12.3`, "force majeure", "notwithstanding" — and learned
    sparse weights handle term importance better than raw BM25 frequency.
    """

    indices: tuple[int, ...] = ()
    values: tuple[float, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.indices)


@dataclass(frozen=True)
class Embedding:
    """Dense and (optionally) sparse, from one model pass.

    One object rather than two calls because bge-m3 produces both in a single
    forward pass; splitting them would double the inference cost.
    """

    dense: tuple[float, ...]
    sparse: SparseVector = field(default_factory=SparseVector)


@dataclass(frozen=True)
class Hit:
    """A retrieved chunk with the score that retrieved it."""

    chunk: IndexedChunk
    score: float
    source: HitSource = "fused"
    #: Set once the reranker has scored it, so the trace keeps both numbers.
    rerank_score: float | None = None

    def with_rerank(self, score: float) -> Hit:
        return Hit(chunk=self.chunk, score=self.score, source="reranked", rerank_score=score)

    @property
    def relevance(self) -> float:
        """The score the product shows and thresholds against. The reranker's
        opinion wins when it has one — that is the point of reranking."""
        return self.rerank_score if self.rerank_score is not None else self.score


@runtime_checkable
class Embedder(Protocol):
    """Dense + sparse embedding. `BAAI/bge-m3`, 1024-dim, 8192-token context.

    The context length is the reason this model was chosen over
    `all-MiniLM-L6-v2`: child chunks run ~500 tokens and parent sections ~3,000,
    and a 256-token model silently drops the rest of each one.
    """

    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[Embedding]: ...

    def embed_query(self, text: str) -> Embedding: ...


@runtime_checkable
class Reranker(Protocol):
    """Cross-encoder rerank. `BAAI/bge-reranker-v2-m3`.

    Runs on **child** chunks, never parents: the cross-encoder takes ~8192
    tokens for query and passage combined, and parent expansion happens after
    reranking for exactly that reason.

    `rerank` returns scores normalised to 0-1, because the refusal floor is a
    product-visible number and a raw logit is not comparable across models.
    """

    name: str

    def rerank(self, query: str, hits: list[Hit], *, top_k: int) -> list[Hit]: ...


@runtime_checkable
class VectorStore(Protocol):
    """Hybrid search plus the filtered delete FR-26 needs."""

    name: str

    def ensure_collection(self, *, dim: int) -> None: ...

    def upsert(self, chunks: list[IndexedChunk], embeddings: list[Embedding]) -> int: ...

    def search(
        self,
        embedding: Embedding,
        *,
        document_ids: list[str],
        top_k: int,
    ) -> list[Hit]:
        """Dense and sparse prefetch fused server-side by RRF.

        `document_ids` is a filter, not a hint: FR-7 and FR-9 mean a chunk
        outside the resolved scope must not be retrievable, and enforcing that
        in the query is the only place it cannot be forgotten.
        """
        ...

    def fetch_parents(
        self, parent_keys: list[str], *, document_ids: list[str]
    ) -> list[IndexedChunk]: ...

    def delete_document(self, document_id: str) -> int: ...

    def count(self) -> int: ...
