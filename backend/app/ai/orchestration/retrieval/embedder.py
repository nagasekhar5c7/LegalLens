"""`BAAI/bge-m3` — dense and learned-sparse vectors from one forward pass.

Why this model, recorded here because it is the decision the rest of retrieval
is built on:

* **8192-token context.** Child chunks run ~500 tokens and parent sections
  ~3,000. `all-MiniLM-L6-v2` truncates at 256 and would silently drop more than
  half of every child chunk — a recall problem with no symptom.
* **Dense *and* sparse from one model.** The sparse head is a learned lexical
  representation, so there is no BM25 index to build, cache or invalidate on
  upload. Legal text leans on exact terms (`§ 12.3`, "force majeure",
  "notwithstanding"), and learned weights handle term importance better than raw
  frequency.
* **MIT licensed**, which a product billing attorneys needs. `jina-embeddings-v3`
  benchmarks well and is CC-BY-NC — unusable here.

The model is ~2.2 GB and loads lazily, on first embed rather than at import, so
importing this package costs nothing and the offline tests never download it.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.ai.orchestration.retrieval.ports import Embedding, SparseVector

log = logging.getLogger("legallens.ai.embed")

MODEL_NAME = "BAAI/bge-m3"
DIM = 1024

#: bge-m3's own maximum. Set to the ceiling rather than the library's 512
#: default because the whole reason for choosing this model was not truncating;
#: the tokeniser pads dynamically to the longest text in a batch, so a high
#: ceiling costs nothing on short chunks.
MAX_PASSAGE_TOKENS = 8192
#: A question is short. Capping it keeps the query pass cheap.
MAX_QUERY_TOKENS = 512


class Bgem3Embedder:
    """Lazily-loaded bge-m3. One instance per process — the model is ~2.2 GB."""

    name = f"{MODEL_NAME}/dense+sparse"
    dim = DIM

    def __init__(
        self,
        *,
        model_name: str = MODEL_NAME,
        use_fp16: bool = False,
        device: str | None = None,
        batch_size: int = 8,
    ) -> None:
        """`use_fp16=False` by default.

        FlagEmbedding defaults it to True, which is right on CUDA and wrong on
        CPU, where several fp16 kernels fall back to slow paths or raise. The
        flag is exposed so a GPU deployment can turn it on deliberately.
        """
        self.model_name = model_name
        self.use_fp16 = use_fp16
        self.device = device
        self.batch_size = batch_size
        self._model: Any | None = None
        # Loading is not thread-safe and the first call may take ~30s; a lock
        # stops two concurrent turns each loading their own 2.2 GB copy.
        self._lock = threading.Lock()

    # ------------------------------------------------------------- loading

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            from FlagEmbedding import BGEM3FlagModel

            log.info("loading %s (first use — may download ~2.2GB)", self.model_name)
            self._model = BGEM3FlagModel(
                self.model_name,
                use_fp16=self.use_fp16,
                devices=self.device,
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False,
                passage_max_length=MAX_PASSAGE_TOKENS,
                query_max_length=MAX_QUERY_TOKENS,
                batch_size=self.batch_size,
            )
            log.info("loaded %s", self.model_name)
            return self._model

    def warm(self) -> None:
        """Load the weights *and* run one inference.

        Constructing `BGEM3FlagModel` does not load the weights — FlagEmbedding
        defers that to the first `encode()`. So a `warm()` that only
        constructed the object left the first real query paying the cold-start
        cost, which measured **17 seconds** against ~70ms warm. The throwaway
        encode is the part that actually warms it.
        """
        self.embed_query("warm")

    # ----------------------------------------------------------- embedding

    def embed_documents(self, texts: list[str]) -> list[Embedding]:
        if not texts:
            return []
        raw = self._load().encode(
            texts,
            max_length=MAX_PASSAGE_TOKENS,
            return_dense=True,
            return_sparse=True,
        )
        return _to_embeddings(raw, len(texts))

    def embed_query(self, text: str) -> Embedding:
        raw = self._load().encode(
            [text],
            max_length=MAX_QUERY_TOKENS,
            return_dense=True,
            return_sparse=True,
        )
        return _to_embeddings(raw, 1)[0]


def _to_embeddings(raw: dict[str, Any], count: int) -> list[Embedding]:
    """Normalise FlagEmbedding's output into `Embedding`.

    `lexical_weights` comes back as a list of `{token_id_as_string: weight}`.
    Qdrant's sparse vectors need integer indices, so the keys are converted
    here — doing it at the store boundary instead would leak the model's
    representation into the store.
    """
    dense = raw["dense_vecs"]
    lexical = raw.get("lexical_weights") or [{}] * count

    out: list[Embedding] = []
    for i in range(count):
        weights = lexical[i] if i < len(lexical) else {}
        # Zero-weight terms carry no signal and only inflate the posting list.
        pairs = [(int(k), float(v)) for k, v in dict(weights).items() if float(v) > 0.0]
        pairs.sort()
        out.append(
            Embedding(
                dense=tuple(float(x) for x in dense[i]),
                sparse=SparseVector(
                    indices=tuple(i for i, _ in pairs),
                    values=tuple(v for _, v in pairs),
                ),
            )
        )
    return out
