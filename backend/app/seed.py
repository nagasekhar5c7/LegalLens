"""Dev seed: one user plus the fixture corpus.

The corpus is not inserted as metadata-only rows. Each fixture document gets
real encrypted page text in the blob store and real `spans` rows whose offsets
were computed against that text — so `GET /api/documents/{id}/pages/{n}` and
`GET /api/spans/{id}` behave identically for a fixture document and an uploaded
one, and a citation chip from a fixture answer actually lands on its clause.

Idempotent: running it twice is a no-op rather than a second corpus.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.fixtures import pages as fixture_pages
from app.ai.fixtures.corpus import CORPUS, FixtureDocument
from app.ai.fixtures.summaries import TEMPLATE_NAME
from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker
from app.logging_setup import configure_logging
from app.models import (
    ChunkMeta,
    Conversation,
    Document,
    DocumentPage,
    DocumentSpan,
    IndexVersion,
    IngestionJob,
    PromptVersion,
    User,
)
from app.models.base import prefixed_id, utcnow
from app.services import auth as auth_service
from app.storage import blob

log = logging.getLogger("legallens.seed")

#: Pages materialised per fixture document. A 312-page MSA does not need all
#: 312 pages generated to be useful — it needs the pages its spans live on,
#: plus a few neighbours so the viewer has somewhere to scroll.
NEIGHBOUR_WINDOW = 1


async def seed(*, reset: bool = False) -> None:
    settings = get_settings()
    settings.blobs_dir.mkdir(parents=True, exist_ok=True)

    async with get_sessionmaker()() as db:
        user = (
            await db.execute(select(User).where(User.email == settings.seed_email))
        ).scalar_one_or_none()

        if user is None:
            user = User(
                email=settings.seed_email.lower(),
                password_hash=auth_service.hash_password(settings.seed_password),
                workspace_name=settings.workspace_name,
            )
            db.add(user)
            await db.flush()
            log.info("created user %s", user.email)
        else:
            log.info("user %s already exists", user.email)

        if reset:
            removed = 0
            for document in list(
                (await db.execute(select(Document).where(Document.user_id == user.id))).scalars()
            ):
                blob.delete_prefix(f"pages/{document.id}")
                blob.delete_prefix(f"files/{document.id}")
                await db.delete(document)
                removed += 1
            for conversation in list(
                (
                    await db.execute(select(Conversation).where(Conversation.user_id == user.id))
                ).scalars()
            ):
                await db.delete(conversation)
            await db.flush()
            log.info("reset: removed %d documents", removed)

        await _seed_versions(db)

        existing = set(
            (await db.execute(select(Document.id).where(Document.user_id == user.id))).scalars()
        )

        created = 0
        for fixture in CORPUS:
            if fixture.id in existing:
                continue
            await _seed_document(db, user.id, fixture)
            created += 1

        await db.commit()
        log.info(
            "seed complete: %d document(s) created, %d already present",
            created,
            len(CORPUS) - created,
        )
        if created:
            _report(settings.seed_email, settings.seed_password)


async def _seed_versions(db: AsyncSession) -> None:
    """NFR-12: the registries exist from the first run, so every generation has
    a version row to point at."""
    if not (await db.execute(select(PromptVersion.id).limit(1))).first():
        for name, version in (("ask", "v4.2.1"), ("summary", "v3.8.0"), ("draft", "v2.4.3")):
            db.add(PromptVersion(name=name, version=version))
    if not (await db.execute(select(IndexVersion.id).limit(1))).first():
        db.add(
            IndexVersion(
                name="fixtures:legallens-m3",
                embedding_model="(none — M3 stub indexer)",
                chunker="section-aware-v1",
                reranker="(none — M3)",
                active=1,
            )
        )
    await db.flush()


async def _seed_document(db: AsyncSession, user_id: str, fixture: FixtureDocument) -> None:
    """One fixture document, with page text and spans that genuinely resolve."""
    uploaded_at = utcnow() - timedelta(hours=fixture.uploaded_hours_ago)

    document = Document(
        id=fixture.id,
        user_id=user_id,
        name=fixture.name,
        ext=".pdf",
        mime="application/pdf",
        # Distinct per fixture so dedupe does not collapse the corpus.
        sha256=blob.sha256_bytes(f"fixture:{fixture.id}".encode()),
        size_bytes=fixture.size_bytes,
        pages=fixture.pages,
        type=fixture.type,
        type_source=fixture.type_source,
        classifier_confidence=fixture.classifier_confidence,
        status=fixture.status,
        progress=fixture.progress,
        failure_reason=(fixture.failure or {}).get("reason"),
        failure_detail=(fixture.failure or {}).get("detail"),
        ocr_json=fixture.ocr,
        uploaded_at=uploaded_at,
    )
    db.add(document)
    await db.flush()

    # The ingestion log the UI's detail drawer renders.
    db.add(
        IngestionJob(
            document_id=document.id,
            status="done" if fixture.status == "ready" else fixture.status,
            step=fixture.status,
            attempt=1,
            steps_json=fixture.ingestion,
            started_at=uploaded_at,
            finished_at=uploaded_at + timedelta(seconds=45)
            if fixture.status in ("ready", "failed")
            else None,
        )
    )

    # A failed document has no indexed text, and inventing some would make the
    # FR-7 exclusion path untestable.
    if fixture.status == "failed":
        await db.flush()
        return

    page_numbers = _pages_to_materialise(fixture)
    doc_offset = 0
    for page_no in page_numbers:
        text = fixture_pages.page_text(fixture.id, page_no)
        key = blob.object_key("pages", document.id, f"p{page_no}")
        blob.write_text(key, text)
        db.add(
            DocumentPage(
                document_id=document.id,
                page_no=page_no,
                char_count=len(text),
                doc_char_start=doc_offset,
                ocr_confidence=fixture_pages.page_ocr_confidence(fixture.id, page_no),
                text_key=key,
                parse_engine="fixture/seed",
            )
        )
        doc_offset += len(text) + 1

    # Spans use the planted ids, so a fixture answer's citation resolves through
    # `GET /api/spans/{id}` exactly as an uploaded document's would.
    span_count = 0
    for span_id in fixture_pages.spans_for_document(fixture.id):
        resolved = fixture_pages.resolve_span(span_id)
        if resolved is None:
            log.warning("fixture span %s did not resolve — skipped", span_id)
            continue
        db.add(
            DocumentSpan(
                id=span_id,
                document_id=document.id,
                page_no=resolved.page,
                char_start=resolved.char_start,
                char_end=resolved.char_end,
                section=resolved.section,
            )
        )
        span_count += 1

        # One chunk per span, so `chunk_meta` is not empty for a document the
        # engine cites — M4's re-index worker walks these rows.
        db.add(
            ChunkMeta(
                id=prefixed_id("ch"),
                document_id=document.id,
                page_from=resolved.page,
                page_to=resolved.page,
                char_start=resolved.char_start,
                char_end=resolved.char_end,
                section_path=resolved.section,
                clause_no=(resolved.section or "").replace("§ ", "") or None,
                token_count=max(1, (resolved.char_end - resolved.char_start) // 4),
                index_version="fixtures:legallens-m3",
                vector_point_id=None,
            )
        )

    await db.flush()
    log.info(
        "seeded %s — %s · %d pages materialised · %d spans",
        fixture.id,
        TEMPLATE_NAME.get(fixture.type, fixture.type),
        len(page_numbers),
        span_count,
    )


def _pages_to_materialise(fixture: FixtureDocument) -> list[int]:
    """Every page a span lives on, plus neighbours, plus the first page."""
    wanted: set[int] = {1}
    for page in fixture_pages.planted_pages(fixture.id):
        for offset in range(-NEIGHBOUR_WINDOW, NEIGHBOUR_WINDOW + 1):
            candidate = page + offset
            if 1 <= candidate <= fixture.pages:
                wanted.add(candidate)
    for page in fixture_pages.LOW_CONFIDENCE.get(fixture.id, []):
        if 1 <= page <= fixture.pages:
            wanted.add(page)
    return sorted(wanted)


def _report(email: str, password: str) -> None:
    print()  # noqa: T201 — this is a CLI
    print("  Legal Lens dev workspace seeded.")  # noqa: T201
    print(f"    email     {email}")  # noqa: T201
    print(f"    password  {password}")  # noqa: T201
    print()  # noqa: T201
    print("  Try:")  # noqa: T201
    print("    curl -c /tmp/ll.txt -X POST localhost:8000/api/auth/login \\")  # noqa: T201
    print("      -H 'content-type: application/json' \\")  # noqa: T201
    print(f'      -d \'{{"email":"{email}","password":"{password}"}}\'')  # noqa: T201
    print("    curl -b /tmp/ll.txt -N localhost:8000/api/ask \\")  # noqa: T201
    print("      -H 'content-type: application/json' \\")  # noqa: T201
    print(
        '      -d \'{"text":"What happens on a change of control?",'
        '"mode":"ask","scope":{"kind":"workspace","documentIds":[]}}\''
    )  # noqa: T201
    print()  # noqa: T201


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Legal Lens dev workspace.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete the seeded user's documents and conversations first",
    )
    args = parser.parse_args()

    configure_logging()
    try:
        await seed(reset=args.reset)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_main())
