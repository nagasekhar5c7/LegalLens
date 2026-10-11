"""`BAAI/bge-reranker-v2-m3` — the cross-encoder that decides what the model sees.

Two things about the ordering in this pipeline.

**It reranks children, never parents.** The cross-encoder scores a (query,
passage) pair inside one window, and a parent section can run ~3,000 tokens.
Parent expansion therefore happens *after* reranking, which is also the stage
order the stream contract already uses: retrieving -> reranking -> expanding.

**Scores are sigmoid-normalised to 0-1.** `FlagReranker(normalize=True)` does
this. It matters because the refusal floor (FR-14) is a product-visible number:
a raw logit is unbounded and not comparable between reranker models, so a
threshold tuned on one would silently mean something else on another.

The floor itself still needs calibrating against a labelled set, which is M5.
Until then it is a configuration knob with a defensible default, not a
principled number, and `settings.py` says so.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.ai.orchestration.retrieval.ports import Hit

log = logging.getLogger("legallens.ai.rerank")

MODEL_NAME = "BAAI/bge-reranker-v2-m3"

#: Query plus passage, per pair. bge-reranker-v2-m3 accepts 8192; child chunks
#: are ~500 tokens, so this is headroom rather than a constraint.
MAX_PAIR_TOKENS = 2048


class Bgem3Reranker:
    """Lazily-loaded cross-encoder. ~2.2 GB, one instance per process."""

    name = MODEL_NAME

    def __init__(
        self,
        *,
        model_name: str = MODEL_NAME,
        use_fp16: bool = False,
        device: str | None = None,
        batch_size: int = 16,
    ) -> None:
        self.model_name = model_name
        self.use_fp16 = use_fp16
        self.device = device
        self.batch_size = batch_size
        self._model: Any | None = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            from FlagEmbedding import FlagReranker

            log.info("loading %s (first use — may download ~2.2GB)", self.model_name)
            self._model = FlagReranker(
                self.model_name,
                use_fp16=self.use_fp16,
                devices=self.device,
                # Sigmoid, so the floor in `settings.py` means the same thing
                # whichever reranker is swapped in behind the port.
                normalize=True,
                max_length=MAX_PAIR_TOKENS,
                batch_size=self.batch_size,
            )
            return self._model

    def warm(self) -> None:
        """Load the weights *and* run one inference.

        Same trap as the embedder: constructing `FlagReranker` is cheap and the
        first `compute_score` pays for it. Measured 5.5s cold against ~39ms per
        pair warm, so without this the first turn of a process looks broken.
        """
        self._score("warm", ["warm"])

    def rerank(self, query: str, hits: list[Hit], *, top_k: int) -> list[Hit]:
        """Score every candidate, return the best `top_k`, highest first.

        Returns the input unchanged when there is nothing to reorder, so a
        single-hit result does not pay a model load.
        """
        if not hits:
            return []
        if len(hits) == 1:
            # Still needs a score: the floor is applied to `relevance`, and an
            # unscored hit would inherit the fusion rank instead.
            scores = self._score(query, [hits[0].chunk.text])
            return [hits[0].with_rerank(scores[0])]

        scores = self._score(query, [h.chunk.text for h in hits])
        scored = [h.with_rerank(s) for h, s in zip(hits, scores, strict=True)]
        scored.sort(key=lambda h: h.rerank_score or 0.0, reverse=True)
        return scored[:top_k]

    def _score(self, query: str, passages: list[str]) -> list[float]:
        raw = self._load().compute_score([(query, p) for p in passages])
        # compute_score returns a bare float for a single pair.
        if isinstance(raw, int | float):
            return [float(raw)]
        return [float(x) for x in raw]
