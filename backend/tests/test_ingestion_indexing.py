"""The ingestion write path: chunk -> embed -> vector store.

Everything here runs against a fake embedder and an in-memory store, so the
suite needs neither torch nor Qdrant. That is the point of the `Embedder` and
`VectorStore` protocols, and it is why these tests can assert on behaviour that
would otherwise only show up as "retrieval got worse" on a real corpus:

  * parents are indexed, not only children (otherwise parent expansion silently
    degrades to naive top-K)
  * `parent_key` is namespaced, so one document's child cannot expand into
    another document's section
  * a parent and its only child share text but not a key
  * identical texts are embedded once
  * re-ingesting replaces its own points instead of accumulating ghosts
  * `chunk_meta.vector_point_id` ends up holding the id the store actually used
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.ai.orchestration.retrieval.ports import Embedding, Hit, IndexedChunk, SparseVector
from app.ai.orchestration.retrieval.writer import (
    DuplicateParentKey,
    IndexWriter,
    point_id_for,
)
from app.db import get_sessionmaker
from app.indexing import QdrantChunkIndexer, chunk_key, to_indexed_chunk
from app.ingestion.ports import Chunk, DocumentRef
from app.models import ChunkMeta
from tests.conftest import FIXTURES, drain_ingestion, upload

DOC = DocumentRef(id="doc_a", name="Acme NDA.pdf", type="contract")
OTHER = DocumentRef(id="doc_b", name="Beta MSA.pdf", type="contract")


# ------------------------------------------------------------------- fakes


class FakeEmbedder:
    """Deterministic, dimension-correct, and it counts its calls.

    The call count is the assertion for the dedupe behaviour: a test that only
    checked the stored vectors could not tell one embed from two.
    """

    name = "fake/embedder"
    dim = 8

    def __init__(self) -> None:
        self.embedded: list[str] = []
        self.batches: list[int] = []

    def _vector(self, text: str) -> Embedding:
        seed = sum(ord(c) for c in text) or 1
        dense = tuple(((seed * (i + 1)) % 97) / 97 for i in range(self.dim))
        return Embedding(dense=dense, sparse=SparseVector(indices=(seed % 500,), values=(1.0,)))

    def embed_documents(self, texts: list[str]) -> list[Embedding]:
        self.embedded.extend(texts)
        self.batches.append(len(texts))
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> Embedding:
        return self._vector(text)


class FakeStore:
    """An in-memory stand-in for Qdrant, with the same filtering contract.

    `search` excludes parents and filters by document id, and `fetch_parents`
    filters by both key and document, because those two filters are exactly
    what the tests below are checking the writer cooperates with.
    """

    name = "fake/store"

    def __init__(self) -> None:
        self.points: dict[str, tuple[IndexedChunk, Embedding]] = {}
        self.collections: list[int] = []

    def ensure_collection(self, *, dim: int) -> None:
        self.collections.append(dim)

    def upsert(self, chunks: list[IndexedChunk], embeddings: list[Embedding]) -> int:
        if len(chunks) != len(embeddings):
            raise ValueError("mismatched lengths")
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            self.points[point_id_for(chunk.chunk_id)] = (chunk, embedding)
        return len(chunks)

    def search(self, embedding: Embedding, *, document_ids: list[str], top_k: int) -> list[Hit]:
        return [
            Hit(chunk=c, score=1.0)
            for c, _ in self.points.values()
            if not c.is_parent and c.document_id in document_ids
        ][:top_k]

    def fetch_parents(
        self, parent_keys: list[str], *, document_ids: list[str]
    ) -> list[IndexedChunk]:
        return [
            c
            for c, _ in self.points.values()
            if c.is_parent and c.parent_key in parent_keys and c.document_id in document_ids
        ]

    def delete_document(self, document_id: str) -> int:
        doomed = [k for k, (c, _) in self.points.items() if c.document_id == document_id]
        for key in doomed:
            del self.points[key]
        return len(doomed)

    def count(self) -> int:
        return len(self.points)


def section(
    *,
    page: int,
    start: int,
    text: str,
    key: str,
    clause: str | None = None,
    parent: bool = False,
) -> Chunk:
    return Chunk(
        page_from=page,
        page_to=page,
        char_start=start,
        char_end=start + len(text),
        text=text,
        section_path=f"§ {clause}" if clause else None,
        clause_no=clause,
        token_count=max(1, len(text) // 4),
        parent_key=key,
        is_parent=parent,
    )


def indexed(document: DocumentRef, chunks: list[Chunk]) -> list[IndexedChunk]:
    return [to_indexed_chunk(document, c) for c in chunks]


@pytest.fixture
def writer() -> tuple[IndexWriter, FakeEmbedder, FakeStore]:
    embedder = FakeEmbedder()
    store = FakeStore()
    return IndexWriter(store=store, embedder=embedder, batch_size=4), embedder, store


# ------------------------------------------------------- the two-path contract


def test_parents_are_indexed_alongside_children(writer: Any) -> None:
    """Parent expansion fetches from the store. A collection of children alone
    makes every child "parent missing" and quietly turns the parent-document
    topology into naive top-K — nothing raises, the answers just get narrower."""
    write, _, store = writer
    chunks = [
        section(
            page=1,
            start=0,
            text="12.1 " + "Confidentiality. " * 200,
            key="p0",
            clause="12.1",
            parent=True,
        ),
        section(page=1, start=0, text="12.1 " + "Confidentiality. " * 100, key="p0", clause="12.1"),
        section(
            page=1, start=1700, text="12.1 " + "Confidentiality. " * 100, key="p0", clause="12.1"
        ),
    ]
    result = write.write(DOC.id, indexed(DOC, chunks))

    assert result.parents == 1
    assert result.children == 2
    assert store.count() == 3

    child = store.search(Embedding(dense=(0.0,) * 8), document_ids=[DOC.id], top_k=5)[0]
    parents = store.fetch_parents([child.chunk.parent_key or ""], document_ids=[DOC.id])
    assert len(parents) == 1, "the child's parent section is not retrievable"
    assert len(parents[0].text) > len(child.chunk.text)


def test_search_never_returns_a_parent(writer: Any) -> None:
    """Parents are in the collection for expansion, not for retrieval: the
    cross-encoder scores a (query, passage) pair in one window and a parent
    section can run 3,000 tokens."""
    write, _, store = writer
    write.write(
        DOC.id,
        indexed(
            DOC,
            [
                section(page=1, start=0, text="A" * 60, key="p0", parent=True),
                section(page=1, start=0, text="A" * 60, key="p0"),
            ],
        ),
    )
    hits = store.search(Embedding(dense=(0.0,) * 8), document_ids=[DOC.id], top_k=10)
    assert hits and all(not h.chunk.is_parent for h in hits)


def test_a_parent_and_its_only_child_do_not_collide(writer: Any) -> None:
    """A section that fits in one chunk is its own child, so the two carry
    identical text, pages and offsets. If `is_parent` were not part of the key
    one would overwrite the other and the collection would lose every parent of
    a short clause."""
    write, _, store = writer
    text = "9.2 Governing law. This agreement is governed by Delaware law."
    chunks = [
        section(page=2, start=100, text=text, key="p3", clause="9.2", parent=True),
        section(page=2, start=100, text=text, key="p3", clause="9.2"),
    ]
    keys = {chunk_key(DOC.id, c) for c in chunks}
    assert len(keys) == 2, f"parent and child share a vector key: {keys}"

    write.write(DOC.id, indexed(DOC, chunks))
    assert store.count() == 2
    assert sum(1 for c, _ in store.points.values() if c.is_parent) == 1


# -------------------------------------------------------------- scope safety


def test_parent_keys_are_namespaced_by_document(writer: Any) -> None:
    """The chunker emits `p0` for every document's first section, and
    `fetch_parents` filters by `parent_key` across every document *in scope*.
    Un-namespaced keys would let one document's child expand into another
    document's section — a citation pointing at a clause nobody asked about."""
    write, _, store = writer
    a = [
        section(page=1, start=0, text="A-parent " * 20, key="p0", parent=True),
        section(page=1, start=0, text="A-child " * 20, key="p0"),
    ]
    b = [
        section(page=1, start=0, text="B-parent " * 20, key="p0", parent=True),
        section(page=1, start=0, text="B-child " * 20, key="p0"),
    ]
    write.write(DOC.id, indexed(DOC, a))
    write.write(OTHER.id, indexed(OTHER, b))

    # Both documents in scope, as a workspace-wide question would have.
    both = [DOC.id, OTHER.id]
    for child, expected in (("A-child", "A-parent"), ("B-child", "B-parent")):
        hit = next(c for c, _ in store.points.values() if not c.is_parent and child in c.text)
        parents = store.fetch_parents([hit.parent_key or ""], document_ids=both)
        assert len(parents) == 1, f"{child} resolved to {len(parents)} parents"
        assert expected in parents[0].text
        assert parents[0].document_id == hit.document_id


def test_a_batch_with_bare_parent_keys_is_refused(writer: Any) -> None:
    """The writer refuses rather than trusting its caller, because the failure it
    prevents — a citation into the wrong document — is worse than a failed
    ingestion."""
    write, _, _ = writer
    bare = IndexedChunk(
        chunk_id="doc_a:C:1-1:0-10",
        document_id=DOC.id,
        document_name=DOC.name,
        document_type=DOC.type,
        page_from=1,
        page_to=1,
        char_start=0,
        char_end=10,
        text="hello",
        parent_key="p0",  # not namespaced
    )
    with pytest.raises(DuplicateParentKey, match="not namespaced"):
        write.write(DOC.id, [bare])


def test_chunks_from_another_document_are_refused(writer: Any) -> None:
    """FR-26's filtered delete finds points by `document_id`. A point written
    under the wrong id would survive its document's deletion."""
    write, _, _ = writer
    with pytest.raises(DuplicateParentKey, match="must be written under its own id"):
        write.write(DOC.id, indexed(OTHER, [section(page=1, start=0, text="x" * 50, key="p0")]))


# ----------------------------------------------------------------- economics


def test_identical_texts_are_embedded_once(writer: Any) -> None:
    """The chunker emits a parent and one child with byte-identical text
    whenever a section fits in a single chunk — the common case in a contract of
    numbered clauses. Embedding the string twice spends CPU on a vector already
    computed."""
    write, embedder, _ = writer
    text = "4.1 Term. This agreement runs for three years."
    chunks = [
        section(page=1, start=0, text=text, key="p0", parent=True),
        section(page=1, start=0, text=text, key="p0"),
        section(page=1, start=60, text="4.2 Renewal. Automatic.", key="p1", parent=True),
        section(page=1, start=60, text="4.2 Renewal. Automatic.", key="p1"),
    ]
    result = write.write(DOC.id, indexed(DOC, chunks))

    assert result.written == 4
    assert result.embedded == 2, "a parent and its identical child were embedded twice"
    assert len(embedder.embedded) == 2
    # Both still get a point, and the reused vector is the same object's values.
    assert len(write.store.points) == 4


def test_batches_respect_the_configured_size(writer: Any) -> None:
    """bge-m3 pads a batch to its longest member, so batch size is a real
    memory and throughput knob rather than a formality."""
    write, embedder, _ = writer
    chunks = [
        section(page=1, start=i * 100, text=f"clause {i} " * 10, key=f"p{i}") for i in range(10)
    ]
    write.write(DOC.id, indexed(DOC, chunks))
    assert embedder.batches == [4, 4, 2]


# -------------------------------------------------------------- re-ingestion


def test_reindexing_replaces_its_own_points(writer: Any) -> None:
    """Point ids are deterministic, so an unchanged chunk overwrites itself —
    but a re-chunk producing *fewer* chunks would leave the surplus behind as
    retrievable ghosts of the previous parse."""
    write, _, store = writer
    first = [
        section(page=1, start=0, text="original " * 30, key="p0", parent=True),
        section(page=1, start=0, text="original " * 15, key="p0"),
        section(page=1, start=300, text="second clause " * 15, key="p1", parent=True),
        section(page=1, start=300, text="second clause " * 15, key="p1"),
    ]
    write.write(DOC.id, indexed(DOC, first))
    assert store.count() == 4

    # A re-parse that found one section instead of two.
    second = [
        section(page=1, start=0, text="original " * 30, key="p0", parent=True),
        section(page=1, start=0, text="original " * 15, key="p0"),
    ]
    result = write.write(DOC.id, indexed(DOC, second))

    assert result.deleted_stale == 4
    assert store.count() == 2, "stale points from the previous parse are still retrievable"
    assert all("second clause" not in c.text for c, _ in store.points.values())


def test_deleting_a_document_leaves_the_others(writer: Any) -> None:
    """FR-26 is a filtered delete, not a collection drop."""
    write, _, store = writer
    write.write(DOC.id, indexed(DOC, [section(page=1, start=0, text="a" * 50, key="p0")]))
    write.write(OTHER.id, indexed(OTHER, [section(page=1, start=0, text="b" * 50, key="p0")]))

    assert write.delete(DOC.id) == 1
    assert store.count() == 1
    assert next(iter(store.points.values()))[0].document_id == OTHER.id


def test_an_empty_chunk_list_is_reported_not_raised(writer: Any) -> None:
    """A document whose every page was blank legitimately produces nothing to
    index. The layout QA gate upstream decides whether that is acceptable; the
    writer's job is to say what it did."""
    write, _, store = writer
    result = write.write(DOC.id, [])
    assert result.written == 0
    assert store.count() == 0
    assert any("no chunks" in w for w in result.warnings)


# ------------------------------------------------------------- the port shape


def test_the_adapter_satisfies_the_ingestion_port() -> None:
    """`app/indexing.py` is the only module importing both layers, so the
    protocol check belongs here rather than inside either package."""
    from app.ingestion.ports import Indexer

    assert isinstance(QdrantChunkIndexer(), Indexer)


def test_the_stub_satisfies_the_ingestion_port() -> None:
    from app.ingestion.ports import Indexer
    from app.ingestion.stub.indexer import StubIndexer

    assert isinstance(StubIndexer(), Indexer)


def test_ingestion_does_not_import_the_ai_layer_through_the_new_seam() -> None:
    """`tests/test_layering.py` enforces this over the whole package; this
    states the intent at the one place the temptation lives, because the
    pipeline now genuinely needs embeddings and the shortest path to them is
    the forbidden import."""
    import ast
    from pathlib import Path

    pipeline = Path(__file__).resolve().parents[1] / "app" / "ingestion" / "pipeline.py"
    tree = ast.parse(pipeline.read_text())
    offenders = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.ai")
    ]
    assert not offenders, f"pipeline.py reaches into the AI layer: {offenders}"


# ----------------------------------------------- end to end, with a fake store


async def test_point_ids_land_on_chunk_meta_rows(
    auth: AsyncClient, worker: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR-12's re-index story needs the join in both directions: given a row,
    find its vector; given a stale index version, find every row to rewrite.

    Runs the real pipeline with the real adapter over the fake store, which is
    the only way to check that the ids the store used are the ids the database
    recorded — the two are derived in different modules.
    """
    import app.indexing as indexing

    embedder = FakeEmbedder()
    store = FakeStore()
    indexer = QdrantChunkIndexer()
    # Bypass the availability probe: the adapter's job here is the translation,
    # and torch is not installed in the test environment.
    indexer._writer = IndexWriter(store=store, embedder=embedder)
    monkeypatch.setattr(indexer, "unavailable_reason", lambda: None)
    monkeypatch.setattr(indexing, "_indexer", indexer)

    document_id = (await upload(auth, FIXTURES / "nda.pdf"))["accepted"][0]["id"]
    await drain_ingestion()

    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(select(ChunkMeta).where(ChunkMeta.document_id == document_id))
            ).scalars()
        )

    assert rows, "nothing was chunked"
    assert all(r.vector_point_id for r in rows), "vector_point_id was not recorded"
    assert {r.vector_point_id for r in rows} == set(store.points), (
        "the ids the database recorded are not the ids the store used"
    )
    assert all("bge-m3" in (r.index_version or "") for r in rows)

    status = (await auth.get(f"/api/documents/{document_id}/status")).json()
    assert status["ingestion"][-1]["label"] == "Embedded & indexed"
    assert "points" in status["ingestion"][-1]["detail"]


# ------------------------------------------------- against a real Qdrant


@pytest.mark.skipif(
    __import__("importlib.util", fromlist=["util"]).find_spec("qdrant_client") is None,
    reason="qdrant-client is not installed (uv sync --extra retrieval)",
)
def test_the_write_path_against_a_real_local_qdrant(tmp_path: Any) -> None:
    """The one test here that uses the real store, with a fake embedder.

    The fakes above check the writer's logic; this checks the half that talks to
    somebody else's API — named vectors, a sparse vector alongside a dense one,
    server-side RRF fusion, a payload index filter and a filtered delete. Those
    are where a qdrant-client version bump breaks things, and no fake would
    notice. The embedder stays fake so the test needs no 2.2 GB download.
    """
    from app.ai.orchestration.retrieval.store import QdrantStore

    store = QdrantStore(path=str(tmp_path / "qdrant"))
    try:
        embedder = FakeEmbedder()
        write = IndexWriter(store=store, embedder=embedder)

        a = [
            section(
                page=1,
                start=0,
                text="12.1 Confidentiality. " * 40,
                key="p0",
                clause="12.1",
                parent=True,
            ),
            section(
                page=1,
                start=0,
                text="12.1 Confidentiality obligations survive.",
                key="p0",
                clause="12.1",
            ),
            section(
                page=2,
                start=0,
                text="9.2 Notice period is thirty (30) days.",
                key="p1",
                clause="9.2",
                parent=True,
            ),
            section(
                page=2,
                start=0,
                text="9.2 Notice period is thirty (30) days.",
                key="p1",
                clause="9.2",
            ),
        ]
        b = [
            section(page=1, start=0, text="B parent section. " * 20, key="p0", parent=True),
            section(page=1, start=0, text="B child clause about termination.", key="p0"),
        ]
        result = write.write(DOC.id, indexed(DOC, a))
        write.write(OTHER.id, indexed(OTHER, b))

        # 4 chunks, 3 distinct texts: § 9.2's parent and child are identical.
        assert result.embedded == 3
        assert store.count() == 6

        query = embedder.embed_query("notice period")
        both = [DOC.id, OTHER.id]
        hits = store.search(query, document_ids=both, top_k=5)
        assert hits
        assert all(not h.chunk.is_parent for h in hits), "a parent was vector-searched"

        parents = store.fetch_parents([h.chunk.parent_key or "" for h in hits], document_ids=both)
        assert parents
        for parent in parents:
            assert parent.document_id in (parent.parent_key or ""), "parent key is not namespaced"

        # FR-7: the scope filter is enforced in the query, not after it.
        assert len(store.search(query, document_ids=[OTHER.id], top_k=5)) == 1
        assert store.search(query, document_ids=[], top_k=5) == []

        # FR-26, and idempotent: a second delete reports 0 rather than failing.
        assert store.delete_document(DOC.id) == 4
        assert store.count() == 2
        assert store.delete_document(DOC.id) == 0
    finally:
        # Local mode holds an exclusive directory lock for the life of the
        # process, so a test that does not close leaks it into every later one.
        store.client.close()
