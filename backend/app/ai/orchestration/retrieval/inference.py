"""One process-wide lock around every torch forward pass in retrieval.

`factory.py` keeps exactly one bge-m3 and one bge-reranker-v2-m3 per process,
and its lock guards *construction*. Nothing guarded *inference*, and the
workflows dispatch `Retriever.retrieve` through `asyncio.to_thread`
(`summary.py`, `draft.py`) — so two turns, or one turn racing the ingestion
indexer, put two worker threads inside torch at the same moment.

That segfaults. Observed on 2026-10-14: three sequential `POST /api/summarize`
calls killed the process with `Fatal Python error: Segmentation fault`, the
faulthandler dump showing `m3.encode_single_device` (embedding, via
`pipeline.py`) and `sdpa_mask` (the cross-encoder, via `rerank.py`) live
simultaneously. The whole backend dies and every in-flight stream is lost.

The lock is shared by *both* models rather than one per model, because that
crash was an embed racing a rerank, not a model racing itself.

**This serialises retrieval across the process.** That is a real cost against
NFR-5's ten concurrent users, and it is the honest trade until inference moves
somewhere it can be parallel safely: a GPU, a hosted reranker, or a dedicated
inference process fronted by a request semaphore. Throughput here is CPU-bound
on one machine anyway, so concurrent turns were contending rather than scaling
— what they gained was a crash, not speed.

Reentrant because `_load()` takes a model's own construction lock while this
one is held, and because a future caller wrapping a whole `retrieve()` should
not deadlock against the leaf calls inside it.
"""

from __future__ import annotations

import threading

#: Held across every `encode()` / `compute_score()` in this package.
#: Acquire it at the leaf call, never around work that waits on the network.
INFERENCE_LOCK = threading.RLock()
