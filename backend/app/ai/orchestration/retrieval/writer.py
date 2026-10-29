"""The write path: chunks in, vector points out.

`pipeline.py` is the read half of retrieval; this is the other half, and the two
have to agree about four things or retrieval degrades silently rather than
failing:

  1. **Parents are indexed, not only children.** `Retriever._expand` fetches a
     parent by `parent_key`; a collection holding children alone makes every
     child "parent missing" and the parent-document topology quietly becomes
     naive top-K. Nothing errors. The answers just get narrower.
  2. **`parent_key` must be unique across documents.** `store.fetch_parents`
     filters by `parent_key` within a *set* of document ids, so a bare `p0` from
     two documents in scope would let one document's child expand into the
     other document's section — a citation pointing at a clause the attorney
     never asked about. The caller namespaces the key; `check_keys()` below
     refuses the batch if it did not.
  3. **Re-indexing replaces, never accumulates.** Point ids are `uuid5` of
     `chunk_id`, so an identical chunk overwrites itself — but a re-chunk that
     produces *fewer* chunks would leave the surplus behind as retrievable
     ghosts of the previous parse. So the document's points are deleted first.
  4. **One embedding per distinct text.** The chunker emits a parent and one
     child with byte-identical text whenever a section fits in a single chunk,
     which is the common case in a contract of numbered clauses. Embedding the
     string twice would spend ~30% more CPU for a vector already computed. The
     dedupe is by exact text, so it cannot change what gets stored — only how
     often the model runs.

There is no API spend on this path. bge-m3 runs locally, so the cost is wall
clock on the ingesting machine, and that is the number worth watching: on CPU
the model does roughly 5-15 chunks a second, which puts a 500-page document in
minutes, not seconds. `IndexWriteResult.embed_ms` reports it per document so
the claim is measured rather than assumed.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field

from app.ai.orchestration.retrieval.ports import Embedder, Embedding, IndexedChunk, VectorStore

log = logging.getLogger("legallens.ai.index")

#: Texts per `embed_documents` call. bge-m3 pads a batch to its longest member,
#: so a large batch mixing a 200-char child with a 12,000-char parent wastes
#: most of the padded width. 16 keeps the padding waste small while still
#: amortising the per-call overhead.
EMBED_BATCH = 16


@dataclass
class IndexWriteResult:
    """What was written, and enough about how to explain it in a trace (NFR-11)."""

    #: Qdrant point ids, aligned to the input chunk list. `None` for a chunk
    #: that was not written, so a caller can store the ids it did get.
    point_ids: list[str | None] = field(default_factory=list)
    children: int = 0
    parents: int = 0
    #: Distinct texts actually embedded, vs. chunks submitted.
    embedded: int = 0
    deleted_stale: int = 0
    embed_ms: float = 0.0
    upsert_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def written(self) -> int:
        return self.children + self.parents


class DuplicateParentKey(ValueError):
    """Raised when two documents in one batch share a `parent_key`.

    A `ValueError` and not a warning: the failure mode it prevents is a citation
    resolving into a document the attorney did not ask about, which is worse
    than a failed ingestion.
    """


class IndexWriter:
    """Embeds chunks and upserts them into the vector store."""

    def __init__(
        self,
        *,
        store: VectorStore,
        embedder: Embedder,
        batch_size: int = EMBED_BATCH,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.batch_size = batch_size

    @property
    def name(self) -> str:
        return f"{self.embedder.name} -> {self.store.name}"

    def write(
        self,
        document_id: str,
        chunks: list[IndexedChunk],
        *,
        replace: bool = True,
    ) -> IndexWriteResult:
        """Index one document's chunks. Parents and children together.

        `replace=True` deletes the document's existing points first. Turning it
        off is for an append that is known not to overlap; for an ordinary
        re-ingestion it would leave stale points retrievable.
        """
        result = IndexWriteResult()
        if not chunks:
            # Not an error: a document whose every page was blank legitimately
            # produces nothing to index, and the layout QA gate upstream is
            # what decides whether that is acceptable.
            result.warnings.append("no chunks submitted — nothing was indexed")
            return result

        _check_keys(document_id, chunks)
        self.store.ensure_collection(dim=self.embedder.dim)

        if replace:
            result.deleted_stale = self.store.delete_document(document_id)

        started = time.perf_counter()
        embeddings, result.embedded = self._embed_deduped([c.text for c in chunks])
        result.embed_ms = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        written = self.store.upsert(chunks, embeddings)
        result.upsert_ms = (time.perf_counter() - started) * 1000

        result.point_ids = [point_id_for(c.chunk_id) for c in chunks]
        result.parents = sum(1 for c in chunks if c.is_parent)
        result.children = len(chunks) - result.parents

        if written != len(chunks):
            # The store reports what it accepted. A mismatch means a point was
            # dropped, and a dropped point is an unretrievable clause, so it is
            # recorded rather than inferred from a count later.
            result.warnings.append(f"store accepted {written} of {len(chunks)} points")

        log.info(
            "indexed %s: %d children + %d parents, %d distinct texts embedded "
            "in %.0fms, upsert %.0fms%s",
            document_id,
            result.children,
            result.parents,
            result.embedded,
            result.embed_ms,
            result.upsert_ms,
            f", replaced {result.deleted_stale} stale point(s)" if result.deleted_stale else "",
        )
        return result

    def delete(self, document_id: str) -> int:
        """FR-26. The filtered delete, effective immediately for retrieval."""
        return self.store.delete_document(document_id)

    # ----------------------------------------------------------- embedding

    def _embed_deduped(self, texts: list[str]) -> tuple[list[Embedding], int]:
        """Embed each distinct text once, then re-expand to the input order.

        Keyed on the exact string. A near-duplicate still gets its own pass,
        which is correct — normalising before hashing would mean two chunks
        with different text shared a vector.
        """
        order: dict[str, int] = {}
        unique: list[str] = []
        for text in texts:
            if text not in order:
                order[text] = len(unique)
                unique.append(text)

        vectors: list[Embedding] = []
        for start in range(0, len(unique), self.batch_size):
            batch = unique[start : start + self.batch_size]
            vectors.extend(self.embedder.embed_documents(batch))

        if len(vectors) != len(unique):
            raise ValueError(
                f"embedder returned {len(vectors)} vectors for {len(unique)} texts — "
                "a mismatch here would attach a vector to the wrong clause"
            )
        return [vectors[order[t]] for t in texts], len(unique)


def point_id_for(chunk_id: str) -> str:
    """The Qdrant point id for a chunk id.

    Exposed because `chunk_meta.vector_point_id` stores it, and a caller that
    derived it differently from the store would record an id that resolves to
    nothing.
    """
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def _check_keys(document_id: str, chunks: list[IndexedChunk]) -> None:
    """Guards invariants 2 and 3 of the module docstring before anything is written."""
    foreign = {c.document_id for c in chunks} - {document_id}
    if foreign:
        raise DuplicateParentKey(
            f"chunks for {sorted(foreign)} were submitted under {document_id} — "
            "a document's points must be written under its own id or FR-26's "
            "filtered delete would miss them"
        )

    bare = sorted(
        {c.parent_key for c in chunks if c.parent_key and document_id not in c.parent_key}
    )
    if bare:
        raise DuplicateParentKey(
            f"parent_key(s) {bare[:5]} are not namespaced by document — "
            "fetch_parents filters by parent_key across every document in "
            "scope, so one document's child would expand into another's section"
        )

    counts = Counter(c.chunk_id for c in chunks)
    collisions = sorted(cid for cid, n in counts.items() if n > 1)
    if collisions:
        raise DuplicateParentKey(
            f"duplicate chunk_id(s) {collisions[:5]} — point ids are derived "
            "from chunk_id, so these chunks would overwrite each other"
        )
