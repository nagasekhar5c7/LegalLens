"""Process-level singletons for the retrieval stack.

This file exists because of two hard constraints that are easy to discover the
expensive way.

**Local Qdrant takes an exclusive lock on its directory.** A second
`QdrantClient(path=...)` in the same process raises
"Storage folder ... is already accessed by another instance". `build_retriever()`
used to construct a fresh store per call, so two concurrent turns — or one turn
racing the ingestion indexer — would have raised rather than queued. One store
per `(path, collection)` is therefore not an optimisation, it is the only
correct arrangement. The ingestion side gets its store from here too, which is
what lets writing and reading share one lock instead of fighting over it.

**The models are ~2.2 GB each.** bge-m3 and bge-reranker-v2-m3 load lazily on
first use, but a second *instance* loads a second copy into RAM. Caching the
instance, not just the weights, is what keeps a laptop usable.

`reset()` is for tests and for a settings change in a long-lived process; it
drops the cached objects and closes the Qdrant client, because an un-closed
local client keeps the lock until the process exits.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from app.ai.orchestration.retrieval.pipeline import Retriever
from app.ai.orchestration.retrieval.ports import Embedder, Reranker, VectorStore
from app.ai.orchestration.settings import RouterSettings, get_router_settings

log = logging.getLogger("legallens.ai.retrieval")

#: Guards construction, not use. Both model classes have their own load lock;
#: this one stops two threads building two clients for the same directory in
#: the window before the first is cached.
_lock = threading.Lock()
_embedder: Embedder | None = None
_reranker: Reranker | None = None
_stores: dict[str, VectorStore] = {}


def _store_key(cfg: RouterSettings) -> str:
    return f"{cfg.qdrant_url or cfg.qdrant_path}#{cfg.qdrant_collection}"


def get_embedder(settings: RouterSettings | None = None) -> Embedder:
    """The shared bge-m3 instance. Cheap to call; the weights load on first embed."""
    global _embedder
    if _embedder is not None:
        return _embedder
    with _lock:
        if _embedder is None:
            from app.ai.orchestration.retrieval.embedder import Bgem3Embedder

            cfg = settings or get_router_settings()
            _embedder = Bgem3Embedder(
                model_name=cfg.embed_model, use_fp16=cfg.model_fp16, device=cfg.model_device
            )
        return _embedder


def get_reranker(settings: RouterSettings | None = None) -> Reranker:
    """The shared cross-encoder. Not used by ingestion — reranking is a read-path
    concern — but cached in the same place so there is one answer to "who owns
    the models"."""
    global _reranker
    if _reranker is not None:
        return _reranker
    with _lock:
        if _reranker is None:
            from app.ai.orchestration.retrieval.rerank import Bgem3Reranker

            cfg = settings or get_router_settings()
            _reranker = Bgem3Reranker(
                model_name=cfg.rerank_model, use_fp16=cfg.model_fp16, device=cfg.model_device
            )
        return _reranker


def get_store(settings: RouterSettings | None = None) -> VectorStore:
    """The shared Qdrant store for the configured path/collection.

    Keyed by path and collection rather than cached outright, so a test can
    point at a tmp_path without inheriting the previous test's directory.
    """
    cfg = settings or get_router_settings()
    key = _store_key(cfg)
    found = _stores.get(key)
    if found is not None:
        return found
    with _lock:
        if key not in _stores:
            from app.ai.orchestration.retrieval.store import QdrantStore

            _stores[key] = QdrantStore(
                path=cfg.qdrant_path,
                url=cfg.qdrant_url,
                collection=cfg.qdrant_collection,
                api_key=cfg.qdrant_api_key,
            )
            log.info("opened vector store %s", _stores[key].name)
        return _stores[key]


def build_retriever(settings: RouterSettings | None = None) -> Retriever:
    """The read path, over the shared store and models."""
    cfg = settings or get_router_settings()
    return Retriever(
        store=get_store(cfg),
        embedder=get_embedder(cfg),
        reranker=get_reranker(cfg),
        candidate_k=cfg.candidate_k,
        rerank_k=cfg.rerank_k,
        relevance_floor=cfg.relevance_floor,
        context_token_budget=cfg.context_token_budget,
    )


def warm_models(settings: RouterSettings | None = None) -> None:
    """Load both models and run one inference through each.

    Blocking and slow by design — call it off the event loop. Constructing
    either model is cheap; the weights load on the first *inference*, so the
    cost lands on whichever turn happens to be first unless something pays it
    up front. Measured cold-start, from the classes' own docstrings: ~17s for
    bge-m3 and ~5.5s for the reranker, against ~70ms and ~39ms warm.

    `warm` is deliberately not on the `Embedder`/`Reranker` protocols. Those
    describe the read contract, and the test doubles that implement them have
    no weights to load; making warming part of the protocol would force a
    no-op on every fake to satisfy a concern none of them have. Hence the
    `getattr`: a model that can be warmed is, and one that cannot is skipped.
    """
    cfg = settings or get_router_settings()
    for label, build in (("embedder", get_embedder), ("reranker", get_reranker)):
        try:
            model: Any = build(cfg)
            warm = getattr(model, "warm", None)
            if warm is None:
                continue
            t = time.perf_counter()
            warm()
            log.info("warmed %s in %.1fs", label, time.perf_counter() - t)
        except Exception:  # noqa: BLE001 — warming is an optimisation, never a reason not to start
            log.warning(
                "could not warm the %s; the first turn will pay for it", label, exc_info=True
            )


def reset() -> None:
    """Drop the cached instances and release the Qdrant lock.

    Closing matters: a local client holds its directory lock for the life of the
    process, so a test that does not close leaves every later test in the same
    process unable to open the same path.
    """
    global _embedder, _reranker
    with _lock:
        for store in _stores.values():
            client: Any = getattr(store, "client", None)
            if client is not None:
                try:
                    client.close()
                except Exception as exc:  # noqa: BLE001 — closing must not fail a teardown
                    log.debug("closing %s: %s", getattr(store, "name", "store"), exc)
        _stores.clear()
        _embedder = None
        _reranker = None
