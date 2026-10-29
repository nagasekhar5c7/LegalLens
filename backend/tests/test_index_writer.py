"""The write path and the process-level singletons.

Two invariants here are worth more than the rest of this file put together.

**`parent_key` must be namespaced per document.** `fetch_parents` filters by
`parent_key` across every document in scope, so a bare "p0" from two documents
lets one document's child expand into the other's section — a citation naming a
clause the attorney never asked about. `Retriever._expand` now keys by
`(document_id, parent_key)` so the read path is safe regardless, and
`IndexWriter._check_keys` refuses the batch at write time. Both, deliberately:
one is a correctness fix and the other stops a bad index being built at all.

**A local Qdrant client takes an exclusive directory lock.** Constructing a
second one for the same path raises, which is why the store is a cached
singleton rather than built per call.
"""

from __future__ import annotations

import pytest

from app.ai.orchestration.retrieval import factory
from app.ai.orchestration.retrieval.ports import Embedding, IndexedChunk, SparseVector
from app.ai.orchestration.retrieval.store import QdrantStore
from app.ai.orchestration.retrieval.writer import (
    DuplicateParentKey,
    IndexWriter,
    point_id_for,
)
from app.ai.orchestration.settings import RouterSettings
from tests.test_retrieval import FakeStore


class CountingEmbedder:
    """Counts how many texts it was asked to embed, to prove the dedupe."""

    name = "counting"
    dim = 4

    def __init__(self) -> None:
        self.seen: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[Embedding]:
        self.seen.extend(texts)
        return [
            Embedding(dense=(0.1, 0.2, 0.3, 0.4), sparse=SparseVector((1,), (1.0,))) for _ in texts
        ]

    def embed_query(self, text: str) -> Embedding:
        return Embedding(dense=(0.1, 0.2, 0.3, 0.4))


def rec(
    doc: str, i: int, text: str, *, pk: str | None = None, parent: bool = False
) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"{doc}:{'par' if parent else 'chi'}:{i}",
        document_id=doc,
        document_name=f"Doc {doc}",
        document_type="contract",
        page_from=i + 1,
        page_to=i + 1,
        char_start=0,
        char_end=len(text),
        text=text,
        parent_key=pk if pk is not None else f"{doc}:p{i}",
        is_parent=parent,
    )


def _writer(store: FakeStore | None = None) -> tuple[IndexWriter, CountingEmbedder]:
    embedder = CountingEmbedder()
    return IndexWriter(store=store or FakeStore(), embedder=embedder), embedder


# ------------------------------------------------------- the key invariants


def test_a_bare_parent_key_is_refused() -> None:
    """The failure it prevents — a citation resolving into the wrong document —
    is worse than a failed ingestion, so this raises rather than warns."""
    writer, _ = _writer()
    with pytest.raises(DuplicateParentKey, match="not namespaced"):
        writer.write("doc1", [rec("doc1", 0, "text", pk="p0")])


def test_a_namespaced_parent_key_is_accepted() -> None:
    writer, _ = _writer()
    result = writer.write("doc1", [rec("doc1", 0, "text", pk="doc1:p0")])
    assert result.written == 1


def test_chunks_belonging_to_another_document_are_refused() -> None:
    """FR-26's filtered delete works by document_id; a chunk written under the
    wrong id would survive its own document's deletion."""
    writer, _ = _writer()
    with pytest.raises(DuplicateParentKey, match="were submitted under"):
        writer.write("doc1", [rec("doc2", 0, "text", pk="doc2:p0")])


def test_duplicate_chunk_ids_are_refused() -> None:
    """Point ids derive from chunk_id, so duplicates silently overwrite."""
    writer, _ = _writer()
    same = rec("doc1", 0, "a", pk="doc1:p0")
    with pytest.raises(DuplicateParentKey, match="duplicate chunk_id"):
        writer.write("doc1", [same, same])


# ------------------------------------------------------------- the write


def test_parents_and_children_are_both_indexed() -> None:
    """`Retriever._expand` fetches parents from the collection. A children-only
    index makes every child "parent missing" and the parent-document topology
    quietly degrades to naive top-K with nothing erroring."""
    writer, _ = _writer()
    result = writer.write(
        "doc1",
        [
            rec("doc1", 0, "child", pk="doc1:p0"),
            rec("doc1", 0, "PARENT SECTION", pk="doc1:p0", parent=True),
        ],
    )
    assert result.children == 1
    assert result.parents == 1
    assert result.written == 2


def test_identical_texts_are_embedded_once() -> None:
    """The chunker emits a parent and one child with byte-identical text
    whenever a section fits in a single chunk — the common case in a contract
    of numbered clauses."""
    writer, embedder = _writer()
    result = writer.write(
        "doc1",
        [
            rec("doc1", 0, "same text", pk="doc1:p0"),
            rec("doc1", 0, "same text", pk="doc1:p0", parent=True),
        ],
    )
    assert result.embedded == 1
    assert embedder.seen == ["same text"]
    assert result.written == 2, "both chunks are still stored"


def test_vectors_are_re_expanded_in_input_order() -> None:
    """A mis-aligned re-expansion would attach a vector to the wrong clause."""
    writer, embedder = _writer()
    writer.write(
        "doc1",
        [
            rec("doc1", 0, "alpha", pk="doc1:p0"),
            rec("doc1", 1, "beta", pk="doc1:p1"),
            rec("doc1", 2, "alpha", pk="doc1:p2"),
        ],
    )
    assert embedder.seen == ["alpha", "beta"]


def test_re_indexing_deletes_the_documents_old_points_first() -> None:
    """A re-chunk producing fewer chunks would otherwise leave the surplus
    behind as retrievable ghosts of the previous parse."""

    class Deleting(FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.deleted: list[str] = []

        def delete_document(self, document_id: str) -> int:
            self.deleted.append(document_id)
            return 3

    store = Deleting()
    writer, _ = _writer(store)
    result = writer.write("doc1", [rec("doc1", 0, "t", pk="doc1:p0")])

    assert store.deleted == ["doc1"]
    assert result.deleted_stale == 3


def test_replace_can_be_turned_off() -> None:
    class Deleting(FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.deleted: list[str] = []

        def delete_document(self, document_id: str) -> int:
            self.deleted.append(document_id)
            return 0

    store = Deleting()
    writer, _ = _writer(store)
    writer.write("doc1", [rec("doc1", 0, "t", pk="doc1:p0")], replace=False)
    assert store.deleted == []


def test_an_empty_batch_warns_rather_than_raising() -> None:
    """A document whose every page was blank legitimately produces nothing;
    the layout-QA gate upstream decides whether that is acceptable."""
    writer, _ = _writer()
    result = writer.write("doc1", [])
    assert result.written == 0
    assert result.warnings


def test_a_short_upsert_is_recorded_not_inferred() -> None:
    class Lossy(FakeStore):
        def upsert(self, chunks, embeddings):  # noqa: ANN001, ANN201
            return len(chunks) - 1

    writer, _ = _writer(Lossy())
    result = writer.write(
        "doc1",
        [rec("doc1", 0, "a", pk="doc1:p0"), rec("doc1", 1, "b", pk="doc1:p1")],
    )
    assert any("accepted 1 of 2" in w for w in result.warnings)


def test_point_ids_are_deterministic_and_match_the_store() -> None:
    """`chunk_meta.vector_point_id` stores this; a caller deriving it
    differently would record an id that resolves to nothing."""
    assert point_id_for("doc1:chi:0") == point_id_for("doc1:chi:0")
    assert point_id_for("doc1:chi:0") != point_id_for("doc1:chi:1")

    import uuid

    assert point_id_for("x") == str(uuid.uuid5(uuid.NAMESPACE_URL, "x"))


def test_point_ids_are_returned_aligned_to_the_input() -> None:
    writer, _ = _writer()
    chunks = [rec("doc1", i, f"t{i}", pk=f"doc1:p{i}") for i in range(3)]
    result = writer.write("doc1", chunks)
    assert result.point_ids == [point_id_for(c.chunk_id) for c in chunks]


def test_an_embedder_returning_the_wrong_count_raises() -> None:
    class Broken(CountingEmbedder):
        def embed_documents(self, texts: list[str]) -> list[Embedding]:
            return []

    writer = IndexWriter(store=FakeStore(), embedder=Broken())
    with pytest.raises(ValueError, match="wrong clause"):
        writer.write("doc1", [rec("doc1", 0, "t", pk="doc1:p0")])


# -------------------------------------------------- the process singletons


def test_the_store_is_shared_per_path_not_rebuilt(tmp_path) -> None:  # noqa: ANN001
    """A local Qdrant client takes an exclusive lock on its directory, so a
    second one for the same path raises. Sharing is the only correct
    arrangement, not an optimisation."""
    settings = RouterSettings(api_key="x", qdrant_path=str(tmp_path / "qd"))
    try:
        first = factory.get_store(settings)
        second = factory.get_store(settings)
        assert first is second
    finally:
        factory.reset()


def test_a_second_client_on_one_directory_really_does_raise(tmp_path) -> None:  # noqa: ANN001
    """The behaviour the singleton exists to avoid, asserted so the reason
    cannot be refactored away by someone who doubts it."""
    path = str(tmp_path / "qd")
    store = QdrantStore(path=path)
    try:
        with pytest.raises(RuntimeError, match="already accessed"):
            QdrantStore(path=path)
    finally:
        store.client.close()


def test_the_models_are_shared(tmp_path) -> None:  # noqa: ANN001
    """Each is ~2.2 GB; a second instance loads a second copy into RAM."""
    settings = RouterSettings(api_key="x", qdrant_path=str(tmp_path / "qd"))
    try:
        assert factory.get_embedder(settings) is factory.get_embedder(settings)
        assert factory.get_reranker(settings) is factory.get_reranker(settings)
    finally:
        factory.reset()


def test_reset_releases_the_lock_so_the_path_can_be_reopened(tmp_path) -> None:  # noqa: ANN001
    """Without closing, every later test in the same process is locked out of
    the same directory."""
    settings = RouterSettings(api_key="x", qdrant_path=str(tmp_path / "qd"))
    first = factory.get_store(settings)
    factory.reset()
    second = factory.get_store(settings)
    try:
        assert first is not second
    finally:
        factory.reset()


def test_build_retriever_wires_the_configured_numbers(tmp_path) -> None:  # noqa: ANN001
    settings = RouterSettings(
        api_key="x",
        qdrant_path=str(tmp_path / "qd"),
        candidate_k=7,
        rerank_k=3,
        relevance_floor=0.4,
        context_token_budget=1234,
    )
    try:
        retriever = factory.build_retriever(settings)
        assert retriever.candidate_k == 7
        assert retriever.rerank_k == 3
        assert retriever.relevance_floor == 0.4
        assert retriever.context_token_budget == 1234
    finally:
        factory.reset()
