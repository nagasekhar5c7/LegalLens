#!/usr/bin/env python
"""Index a folder of documents into Qdrant, with no backend involved.

The real chunker and the real embedder, straight into the real vector store —
no database, no FastAPI, no migrations. This is what makes the ask workflow
testable before integration.

    uv run python scripts/index_corpus.py ../docs            # a folder
    uv run python scripts/index_corpus.py a.pdf b.docx       # specific files
    uv run python scripts/index_corpus.py --stats            # what is indexed
    uv run python scripts/index_corpus.py --clear            # drop everything

First run downloads bge-m3 (~2.2 GB). It is cached by Hugging Face afterwards.

Document ids are derived from the file's SHA-256, so re-indexing the same file
replaces its points instead of duplicating them, and the id is stable across
runs — which is what lets `try_ask.py` scope a question by name.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.orchestration.retrieval import factory  # noqa: E402
from app.ai.orchestration.retrieval.ports import IndexedChunk  # noqa: E402
from app.ai.orchestration.retrieval.writer import IndexWriter  # noqa: E402
from app.ai.orchestration.settings import get_router_settings  # noqa: E402
from app.ingestion.extract.docx_parser import DocxParser  # noqa: E402
from app.ingestion.extract.pdf import PdfParser  # noqa: E402
from app.ingestion.extract.txt import TxtParser  # noqa: E402
from app.ingestion.stub.chunker import SectionAwareChunker  # noqa: E402
from app.ingestion.stub.classifier import StubClassifier  # noqa: E402

BOLD, DIM, GREEN, YELLOW, RED, RESET = (
    "\033[1m",
    "\033[2m",
    "\033[32m",
    "\033[33m",
    "\033[31m",
    "\033[0m",
)

PARSERS = (PdfParser(), DocxParser(), TxtParser())
SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


def document_id_for(path: Path) -> str:
    """Stable across runs, so re-indexing replaces rather than accumulates."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"doc_{digest}"


def _parse(path: Path, max_pages: int):  # noqa: ANN201
    for parser in PARSERS:
        if parser.supports(path.suffix.lower()):
            return parser.parse(str(path), max_pages=max_pages)
    raise ValueError(f"no parser for {path.suffix}")


def to_indexed(document_id: str, name: str, doc_type: str, chunks: list) -> list[IndexedChunk]:
    """Chunker output -> store records, with `parent_key` namespaced.

    The namespacing is not cosmetic. `SectionAwareChunker` numbers parents per
    document — bare "p0", "p1", restarting at zero for each file — and
    `fetch_parents` filters by `parent_key` across every document in scope. Two
    documents both holding "p0" would let one document's child expand into the
    other's section, producing a citation to a clause the attorney never asked
    about. `IndexWriter._check_keys` refuses a batch that forgot this.
    """
    out: list[IndexedChunk] = []
    for i, chunk in enumerate(chunks):
        parent_key = f"{document_id}:{chunk.parent_key}" if chunk.parent_key else None
        kind = "par" if chunk.is_parent else "chi"
        out.append(
            IndexedChunk(
                chunk_id=f"{document_id}:{kind}:{i}",
                document_id=document_id,
                document_name=name,
                document_type=doc_type,
                page_from=chunk.page_from,
                page_to=chunk.page_to,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                text=chunk.text,
                section_path=chunk.section_path,
                clause_no=chunk.clause_no,
                parent_key=parent_key,
                is_parent=chunk.is_parent,
            )
        )
    return out


def collect(targets: list[str]) -> list[Path]:
    found: list[Path] = []
    for target in targets:
        path = Path(target).expanduser()
        if path.is_dir():
            found.extend(p for p in sorted(path.rglob("*")) if p.suffix.lower() in SUFFIXES)
        elif path.is_file():
            found.append(path)
        else:
            print(f"{YELLOW}skipping {path} — not found{RESET}", file=sys.stderr)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", help="files or folders to index")
    parser.add_argument("--max-pages", type=int, default=500)
    parser.add_argument("--stats", action="store_true", help="show what is indexed")
    parser.add_argument("--clear", action="store_true", help="delete the collection")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    cfg = get_router_settings()
    store = factory.get_store(cfg)

    if args.clear:
        from qdrant_client import QdrantClient  # noqa: F401

        client = store.client  # type: ignore[attr-defined]
        if client.collection_exists(store.collection):  # type: ignore[attr-defined]
            client.delete_collection(store.collection)  # type: ignore[attr-defined]
            print(f"{GREEN}deleted collection {store.collection}{RESET}")
        else:
            print("nothing to delete")
        return 0

    if args.stats or not args.targets:
        print(f"{BOLD}{store.name}{RESET}")
        print(f"  collection : {cfg.qdrant_collection}")
        print(f"  points     : {store.count()}")
        print(f"  embedder   : {cfg.embed_model} ({cfg.candidate_k} candidates/query)")
        print(f"  reranker   : {cfg.rerank_model} (top {cfg.rerank_k})")
        if not args.targets:
            print(f"\n{DIM}pass files or a folder to index them{RESET}")
        return 0

    paths = collect(args.targets)
    if not paths:
        print(f"{RED}nothing to index{RESET}", file=sys.stderr)
        return 1

    print(f"{BOLD}Indexing {len(paths)} file(s){RESET}  {DIM}{store.name}{RESET}\n")
    print(f"{DIM}loading {cfg.embed_model} (first run downloads ~2.2GB)...{RESET}")
    embedder = factory.get_embedder(cfg)
    embedder.warm()  # type: ignore[attr-defined]

    writer = IndexWriter(store=store, embedder=embedder)
    chunker = SectionAwareChunker()
    classifier = StubClassifier()

    total_chunks = 0
    failures = 0
    started = time.perf_counter()

    for path in paths:
        try:
            parsed = _parse(path, args.max_pages)
            doc_id = document_id_for(path)
            classification = classifier.classify(path.name, parsed.pages)
            chunks = chunker.chunk(parsed.pages, classification.doc_type)
            records = to_indexed(doc_id, path.name, classification.doc_type, chunks)
            result = writer.write(doc_id, records)
        except Exception as exc:  # noqa: BLE001 — one bad file must not stop the batch
            failures += 1
            print(f"  {RED}FAIL{RESET} {path.name}: {type(exc).__name__}: {exc}")
            continue

        total_chunks += result.written
        print(
            f"  {GREEN}ok{RESET}   {path.name:<44} "
            f"{parsed.page_count:>4}p  "
            f"{result.children:>4} child + {result.parents:>3} parent  "
            f"{DIM}{classification.doc_type} · embed {result.embed_ms:.0f}ms{RESET}"
        )
        for warning in result.warnings:
            print(f"       {YELLOW}{warning}{RESET}")

    elapsed = time.perf_counter() - started
    print(
        f"\n{BOLD}{total_chunks} points from {len(paths) - failures}/{len(paths)} "
        f"file(s) in {elapsed:.1f}s{RESET}  ·  collection now holds {store.count()}"
    )
    if failures:
        print(f"{YELLOW}{failures} file(s) failed{RESET}")
    print(f'\n{DIM}now ask:  uv run python scripts/try_ask.py "your question"{RESET}')
    return 1 if failures and total_chunks == 0 else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        # A local Qdrant client holds an exclusive lock on its directory and
        # its __del__ runs too late — during interpreter shutdown, where the
        # import system is already gone and it raises a confusing ImportError
        # traceback after the script has otherwise succeeded.
        factory.reset()
    raise SystemExit(code)
