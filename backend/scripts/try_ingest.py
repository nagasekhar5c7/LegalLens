#!/usr/bin/env python
"""Run the real ingestion pipeline over a file, end to end, and report it.

The live counterpart to `tests/test_ingestion_ocr.py` and
`tests/test_ingestion_indexing.py`, which run offline against fakes. This one
uses whatever is actually installed: a real OCR engine if there is one, real
bge-m3 embeddings into a real Qdrant if the retrieval extra is there.

    # what would run, without ingesting anything
    uv run python scripts/try_ingest.py --check

    # one file, through every step
    uv run python scripts/try_ingest.py ~/Documents/nda.pdf

    # force a branch to see the other path
    uv run python scripts/try_ingest.py --ocr tesseract --indexer qdrant contract.pdf

    # and then ask it something, if retrieval is installed
    uv run python scripts/try_ingest.py --query "what is the notice period?" nda.pdf

It writes into the configured LL_DATA_DIR and LL_DATABASE_URL, so point those
at a scratch directory before running it over anything real:

    LL_DATA_DIR=/tmp/ll LL_DATABASE_URL=sqlite+aiosqlite:////tmp/ll/x.db \\
      uv run python scripts/try_ingest.py file.pdf

First run with the retrieval extra downloads ~4.5 GB of model weights and takes
a few minutes before a single chunk is embedded. That is not a hang.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


def report_environment() -> int:
    """What `--check` prints: which engines would run, and why not the others."""
    from app.config import get_settings
    from app.indexing import describe as describe_index
    from app.ingestion.ocr import describe as describe_ocr

    settings = get_settings()
    ocr = describe_ocr(settings.ocr_engine, dpi=settings.ocr_dpi, languages=settings.ocr_languages)
    index = describe_index()

    print("OCR")
    print(f"  configured   {ocr['configured']}")
    print(f"  resolved     {ocr['resolved']}  {'(real)' if ocr['real'] else '(NO OCR)'}")
    for name, status in dict(ocr["candidates"]).items():  # type: ignore[call-overload]
        print(f"    {name:<44} {status}")
    print("Index")
    print(f"  configured   {index['configured']}")
    print(f"  resolved     {index['resolved']}")
    print(f"  version      {index['indexVersion']}")
    print(f"  vectors      {index['vectors']}")

    if not ocr["real"]:
        print("\nScanned PDFs will be quarantined, not indexed.")
    if index["vectors"] != "available":
        print("Documents will parse but will not be retrievable.")
    return 0


async def ingest(path: Path, *, query: str | None) -> int:
    """Upload through the real HTTP route, then run the real worker pool.

    Through the API and not by writing rows directly: validation, the dedupe
    check, the per-workspace limits and the job row are all part of step 1, and
    a script that reproduced them would be testing its own copy of the pipeline
    rather than the pipeline. `tests/conftest.py` uploads the same way.
    """
    import mimetypes

    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select

    from app.config import get_settings
    from app.db import get_engine, get_sessionmaker
    from app.models import Base, ChunkMeta, Document, DocumentPage, IngestionJob, User
    from app.services import auth as auth_service
    from app.start import create_app

    settings = get_settings()
    settings.blobs_dir.mkdir(parents=True, exist_ok=True)

    # The script owns its schema: it is meant to be pointed at a scratch
    # database, and requiring `make migrate` first would make it awkward to
    # throw away.
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with get_sessionmaker()() as db:
        user = (
            await db.execute(select(User).where(User.email == settings.seed_email))
        ).scalar_one_or_none()
        if user is None:
            db.add(
                User(
                    email=settings.seed_email,
                    password_hash=auth_service.hash_password(settings.seed_password),
                    workspace_name=settings.workspace_name,
                )
            )
            await db.commit()

    # `create_app()` without its lifespan, so the worker pool is started below
    # and drained deliberately rather than racing this script's reads.
    app = create_app()
    # Off the loop, as the pipeline does with every other file read: a 50 MB
    # upload read inline would block the one event loop this script has.
    data = await asyncio.to_thread(path.read_bytes)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://ingest", timeout=60.0
    ) as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": settings.seed_email, "password": settings.seed_password},
        )
        if login.status_code != 200:
            print(f"login failed: {login.status_code} {login.text}", file=sys.stderr)
            return 1

        response = await client.post("/api/documents", files=[("files", (path.name, data, mime))])
        if response.status_code != 201:
            print(f"upload refused: {response.status_code} {response.text}", file=sys.stderr)
            return 1
        body = response.json()

    if not body.get("accepted"):
        for rejected in body.get("rejected", []):
            print(f"rejected: {rejected}", file=sys.stderr)
        return 1
    document_id = body["accepted"][0]["id"]
    print(f"queued {path.name} as {document_id} ({len(data) / 1024:.0f} KB)")

    from app.ingestion.worker import drain, start_worker, stop_worker

    started = time.perf_counter()
    await start_worker()
    try:
        if not await drain(timeout_s=1800):
            print("the queue did not drain in 30 minutes", file=sys.stderr)
            return 1
    finally:
        await stop_worker()
    elapsed = time.perf_counter() - started

    async with get_sessionmaker()() as db:
        document = (
            await db.execute(select(Document).where(Document.id == document_id))
        ).scalar_one()
        pages = list(
            (
                await db.execute(
                    select(DocumentPage)
                    .where(DocumentPage.document_id == document_id)
                    .order_by(DocumentPage.page_no)
                )
            ).scalars()
        )
        chunks = list(
            (
                await db.execute(select(ChunkMeta).where(ChunkMeta.document_id == document_id))
            ).scalars()
        )
        job = (
            (await db.execute(select(IngestionJob).where(IngestionJob.document_id == document_id)))
            .scalars()
            .first()
        )

    print(f"\nstatus       {document.status}  in {elapsed:.1f}s")
    if document.failure_reason:
        print(f"failure      {document.failure_reason}")
        print(f"             {document.failure_detail}")
    print(f"type         {document.type} ({document.classifier_confidence:.2f})")
    print(f"pages        {document.pages}")
    if document.ocr_json:
        print(f"ocr          {json.dumps(document.ocr_json)}")

    print("\nsteps")
    for step in (job.steps_json if job else []) or []:
        ms = f"{step.get('ms', 0)}ms" if "ms" in step else ""
        print(f"  [{step['status']:<7}] {step['label']:<34} {ms:>9}  {step.get('detail', '')}")

    print("\npages")
    for page in pages[:12]:
        confidence = (
            f"conf {page.ocr_confidence:.2f}" if page.ocr_confidence is not None else "text layer"
        )
        image = " +image" if page.image_key else ""
        print(
            f"  p{page.page_no:<4} {page.char_count:>7} chars  {confidence:<12}"
            f"{page.parse_engine or '':<42}{image}"
        )
    if len(pages) > 12:
        print(f"  ... {len(pages) - 12} more")

    parents = sum(1 for c in chunks if c.parent_chunk_id is None)
    vectors = sum(1 for c in chunks if c.vector_point_id)
    print(
        f"\nchunks       {len(chunks)} rows ({parents} parents) · "
        f"{vectors} with a vector point · version "
        f"{chunks[0].index_version if chunks else '—'}"
    )

    if query:
        await ask(query, document_id)
    return 0 if document.status == "ready" else 1


async def ask(query: str, document_id: str) -> None:
    """Retrieve against what was just indexed, so the write path is checked by
    the read path rather than by its own counters."""
    try:
        from app.ai.orchestration import build_retriever
    except ImportError as exc:
        print(f"\nretrieval is not installed: {exc}")
        return

    print(f'\nretrieving "{query}"')
    retriever = build_retriever()
    result = await asyncio.to_thread(retriever.retrieve, query, document_ids=[document_id])
    if result.found_nothing:
        print(
            f"  nothing cleared the floor (best {result.best_relevance}) "
            f"over {result.candidate_count} candidates"
        )
        return
    for hit in result.hits[:5]:
        print(f"  {hit.relevance:.3f}  {hit.chunk.citation_label}")
        print(f"         {hit.chunk.text[:140].replace(chr(10), ' ')}…")
    print(f"  context: {len(result.context)} parents, {result.context_tokens} tokens")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", type=Path, help="a PDF, DOCX or TXT to ingest")
    parser.add_argument("--check", action="store_true", help="report the resolved engines and exit")
    parser.add_argument("--ocr", help="override LL_OCR_ENGINE for this run")
    parser.add_argument("--indexer", help="override LL_INDEXER for this run")
    parser.add_argument("--dpi", type=int, help="override LL_OCR_DPI")
    parser.add_argument("--images", help="override LL_PAGE_IMAGES: scanned | all | off")
    parser.add_argument("--query", help="retrieve this after ingesting")
    args = parser.parse_args()

    # Set before `app.config` is first imported, because settings are cached.
    for flag, variable in (
        (args.ocr, "LL_OCR_ENGINE"),
        (args.indexer, "LL_INDEXER"),
        (args.dpi, "LL_OCR_DPI"),
        (args.images, "LL_PAGE_IMAGES"),
    ):
        if flag is not None:
            os.environ[variable] = str(flag)

    if args.check or args.file is None:
        code = report_environment()
        if args.file is None and not args.check:
            print("\nnothing to ingest — pass a file, or --check to see the environment")
        return code

    if not args.file.is_file():
        print(f"{args.file} is not a file", file=sys.stderr)
        return 2
    return asyncio.run(ingest(args.file, query=args.query))


if __name__ == "__main__":
    raise SystemExit(main())
