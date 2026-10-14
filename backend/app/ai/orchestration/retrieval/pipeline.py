"""The retrieval topology, in one place: search -> rerank -> expand.

    query embedding
      -> Qdrant hybrid (dense + sparse, RRF fused)   top 25 children
      -> bge-reranker-v2-m3                          top 10 children
      -> floor check                                 refuse if nothing clears
      -> parent expansion, deduped, token-budgeted   what the model sees

Three things this file is responsible for and the stages around it are not.

**The floor is applied to reranker scores only.** RRF produces ranks, not
relevances; thresholding a fused rank would mean "position 7" not "weakly
related". So the refusal decision (FR-14) happens after rerank, never before.

**Parents are deduped and budgeted.** Ten children routinely expand to fewer
parents, and a parent runs up to 12,000 characters. Without a token budget the
assembled context is whatever the document happened to contain, which makes
cost unpredictable and can overflow the window. Lowest-ranked parents are
dropped first, and what was dropped is reported rather than silently lost.

**A child whose parent is missing keeps itself.** An index written by an older
chunker, or a parent deleted out from under a child, must degrade to "cite the
child" rather than lose the evidence entirely.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from app.ai.orchestration.retrieval.ports import (
    Embedder,
    Hit,
    IndexedChunk,
    Reranker,
    VectorStore,
)

log = logging.getLogger("legallens.ai.retrieval")


def _estimate_tokens(text: str) -> int:
    """~4 characters per token for English prose.

    An estimate and not `count_tokens`: this runs per candidate on the latency
    path, and the budget it feeds is a guard rail rather than a billing figure.
    Actual usage comes back from the API on every call.
    """
    return max(1, len(text) // 4)


@dataclass
class RetrievalResult:
    """What retrieval found, and enough about how to explain it in a trace."""

    #: Reranked children, best first. The excerpts the UI shows (FR-12).
    hits: list[Hit] = field(default_factory=list)
    #: Reranked children that fell **below** the floor, best first.
    #:
    #: Kept rather than discarded because this is the half of the candidate set
    #: a retrieval failure is diagnosed from. "Nothing was relevant enough" and
    #: "the right clause was retrieved and scored 0.24 against a floor of 0.25"
    #: are different problems with different fixes, and only the second is
    #: visible if the rejects survive. NFR-11 asks for the candidate set with
    #: scores; the kept ones alone are not that set.
    rejected: list[Hit] = field(default_factory=list)
    #: Deduped parent sections, in child-rank order. What generation reads.
    context: list[IndexedChunk] = field(default_factory=list)
    candidate_count: int = 0
    best_relevance: float | None = None
    #: Parents dropped by the token budget, so the trace can say so.
    dropped_parents: int = 0
    context_tokens: int = 0
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def found_nothing(self) -> bool:
        return not self.hits


class Retriever:
    """Hybrid retrieval with rerank and parent expansion."""

    def __init__(
        self,
        *,
        store: VectorStore,
        embedder: Embedder,
        reranker: Reranker,
        candidate_k: int = 25,
        rerank_k: int = 10,
        relevance_floor: float = 0.25,
        context_token_budget: int = 18_000,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.reranker = reranker
        self.candidate_k = candidate_k
        self.rerank_k = rerank_k
        self.relevance_floor = relevance_floor
        self.context_token_budget = context_token_budget

    def retrieve(self, query: str, *, document_ids: list[str]) -> RetrievalResult:
        timings: dict[str, float] = {}

        # FR-7: an empty scope means nothing is retrievable. `QdrantStore`
        # enforces this too, but a product invariant must not depend on one
        # implementation remembering it — a store that forgot would query
        # unfiltered and retrieve documents the scope resolver excluded.
        if not document_ids:
            return RetrievalResult(candidate_count=0, timings_ms=timings)

        t = time.perf_counter()
        embedding = self.embedder.embed_query(query)
        timings["embed_ms"] = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        candidates = self.store.search(embedding, document_ids=document_ids, top_k=self.candidate_k)
        timings["search_ms"] = (time.perf_counter() - t) * 1000

        if not candidates:
            return RetrievalResult(candidate_count=0, timings_ms=timings)

        t = time.perf_counter()
        reranked = self.reranker.rerank(query, candidates, top_k=self.rerank_k)
        timings["rerank_ms"] = (time.perf_counter() - t) * 1000

        best = max((h.relevance for h in reranked), default=None)
        kept = [h for h in reranked if h.relevance >= self.relevance_floor]
        rejected = [h for h in reranked if h.relevance < self.relevance_floor]
        if not kept:
            # FR-14: a structured refusal naming what was searched, not an
            # answer assembled from weakly-related text. `best_relevance` and
            # the floor both ride on the refusal so it can explain itself.
            return RetrievalResult(
                rejected=rejected,
                candidate_count=len(candidates),
                best_relevance=best,
                timings_ms=timings,
            )

        t = time.perf_counter()
        context, dropped, tokens = self._expand(kept, document_ids=document_ids)
        timings["expand_ms"] = (time.perf_counter() - t) * 1000

        return RetrievalResult(
            hits=kept,
            rejected=rejected,
            context=context,
            candidate_count=len(candidates),
            best_relevance=best,
            dropped_parents=dropped,
            context_tokens=tokens,
            timings_ms=timings,
        )

    # ------------------------------------------------------------- expansion

    def _expand(
        self, hits: list[Hit], *, document_ids: list[str]
    ) -> tuple[list[IndexedChunk], int, int]:
        """Children -> parent sections, deduped, in child-rank order.

        Rank order is preserved because it is also the order generation reads
        the context in, and the most relevant passage should not be buried at
        the bottom of the prompt.
        """
        keys = [h.chunk.parent_key for h in hits if h.chunk.parent_key]
        # Keyed by (document_id, parent_key), never by parent_key alone.
        #
        # `SectionAwareChunker` numbers parents per document — bare "p0", "p1",
        # restarting at zero for each file — and `fetch_parents` filters by
        # parent_key across *every* document in scope. Keyed on parent_key
        # only, two documents both holding "p0" collide and the last one
        # returned wins, so a child from the NDA expands into the lease's
        # section and the citation names a clause the attorney never asked
        # about. It is also order-dependent on Qdrant's scroll, so it would
        # reproduce intermittently.
        parents_by_key: dict[tuple[str, str], IndexedChunk] = {}
        if keys:
            for parent in self.store.fetch_parents(
                list(dict.fromkeys(keys)), document_ids=document_ids
            ):
                if parent.parent_key:
                    parents_by_key[(parent.document_id, parent.parent_key)] = parent

        ordered: list[IndexedChunk] = []
        seen: set[str] = set()
        for hit in hits:
            key = hit.chunk.parent_key
            # A child with no retrievable parent still carries its own text —
            # losing the evidence would be worse than citing the narrower span.
            chosen = parents_by_key.get((hit.chunk.document_id, key)) if key else None
            chunk = chosen or hit.chunk
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            ordered.append(chunk)

        kept: list[IndexedChunk] = []
        used = 0
        dropped = 0
        for chunk in ordered:
            cost = _estimate_tokens(chunk.text)
            if kept and used + cost > self.context_token_budget:
                dropped += 1
                continue
            kept.append(chunk)
            used += cost

        if dropped:
            log.info(
                "context budget %d tokens reached: kept %d parents, dropped %d",
                self.context_token_budget,
                len(kept),
                dropped,
            )
        return kept, dropped, used
