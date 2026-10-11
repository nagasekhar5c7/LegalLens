"""Test harness.

Each test gets its own SQLite file and its own blob directory, so nothing
leaks between tests and a failure never leaves a half-written workspace behind.
The ingestion worker is *not* started by default — tests that want it ask for
the `worker` fixture, because a background pool racing the assertions is the
classic source of flaky ingestion tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app import db as db_module
from app.config import Settings, get_settings


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """A fresh, isolated configuration per test."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("LL_ENV", "test")
    monkeypatch.setenv("LL_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("LL_DATA_DIR", str(data_dir))
    # Fixed keys: a per-test random key is fine, but a fixed one makes a failure
    # reproducible by hand against the test's tmp_path.
    monkeypatch.setenv("LL_BLOB_KEY", "11" * 32)
    monkeypatch.setenv("LL_SECRET_KEY", "22" * 32)
    monkeypatch.setenv("LL_INGEST_CONCURRENCY", "1")
    monkeypatch.setenv("LL_SSE_KEEPALIVE_S", "30")
    # The engine's realistic delays are deliberate; they just should not dominate
    # the suite. `test_timing.py` asserts the real factor separately.
    monkeypatch.setenv("LL_STREAM_DELAY_FACTOR", "0.02")
    # The ingestion engines are pinned rather than left on `auto`, because
    # `auto` resolves against what happens to be installed on the machine
    # running the suite. A developer with `brew install tesseract` would then
    # see `scanned.pdf` reach `ready` while CI sees it quarantined — the same
    # test asserting two different things depending on the host. Tests that
    # want a real engine ask for it explicitly and skip when it is absent.
    monkeypatch.setenv("LL_OCR_ENGINE", "stub")
    monkeypatch.setenv("LL_INDEXER", "stub")
    # Rasterising every scanned page would add seconds per upload test for an
    # artefact nothing in the suite reads. `test_ingestion_ocr.py` turns it on.
    monkeypatch.setenv("LL_PAGE_IMAGES", "off")

    get_settings.cache_clear()
    resolved = get_settings()
    yield resolved
    get_settings.cache_clear()
    # The indexer is cached per process and the real one holds a Qdrant
    # directory lock, so a test that resolved one must drop it or the next test
    # inherits a store pointing at the previous test's tmp_path.
    from app.indexing import reset_indexer

    reset_indexer()


@pytest.fixture
async def engine(settings: Settings) -> AsyncIterator[None]:
    """Schema created from the models rather than through Alembic: a migration
    run per test would dominate the suite's runtime. `test_migrations.py`
    asserts the two agree."""
    from app.models import Base

    await db_module.dispose_engine()
    eng = db_module.get_engine()
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await db_module.dispose_engine()


@pytest.fixture
async def app(engine: None, settings: Settings):
    """The real app, with lifespan skipped so no worker pool starts."""
    from app.start import create_app

    created = create_app()
    settings.blobs_dir.mkdir(parents=True, exist_ok=True)
    return created


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0
    ) as ac:
        yield ac


@pytest.fixture
async def user(engine: None, settings: Settings):
    """One user, created directly — login is tested separately."""
    from app.db import get_sessionmaker
    from app.models import User
    from app.services import auth as auth_service

    async with get_sessionmaker()() as session:
        created = User(
            email=settings.seed_email,
            password_hash=auth_service.hash_password(settings.seed_password),
            workspace_name=settings.workspace_name,
        )
        session.add(created)
        await session.commit()
        await session.refresh(created)
        return created


@pytest.fixture
async def auth(client: AsyncClient, user, settings: Settings) -> AsyncClient:
    """A signed-in client. The cookie jar persists across requests."""
    response = await client.post(
        "/api/auth/login",
        json={"email": settings.seed_email, "password": settings.seed_password},
    )
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
async def corpus(auth: AsyncClient, user):
    """The seeded fixture corpus, with real page text and spans."""
    from app.seed import seed

    await seed()
    return auth


@pytest.fixture
async def worker(engine: None) -> AsyncIterator[None]:
    """Start the ingestion pool for tests that need documents to reach `ready`."""
    from app.ingestion.worker import start_worker, stop_worker

    await start_worker()
    yield
    await stop_worker()


# ------------------------------------------------------------ real documents

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def nda_pdf() -> Path:
    """A real two-page PDF with a real text layer."""
    return FIXTURES / "nda.pdf"


@pytest.fixture
def amendment_docx() -> Path:
    """A real DOCX, including a fee table."""
    return FIXTURES / "amendment.docx"


@pytest.fixture
def notes_txt() -> Path:
    return FIXTURES / "notes.txt"


@pytest.fixture
def scanned_pdf() -> Path:
    """A valid PDF with no readable text layer — the quarantine path."""
    return FIXTURES / "scanned.pdf"


async def upload(client: AsyncClient, *paths: Path) -> dict:
    """Upload one or more real files in a single multipart request."""
    import mimetypes

    files = []
    handles = []
    try:
        for path in paths:
            handle = path.open("rb")
            handles.append(handle)
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            files.append(("files", (path.name, handle, mime)))
        response = await client.post("/api/documents", files=files)
    finally:
        for handle in handles:
            handle.close()
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------- SSE helpers


async def collect_stream(
    client: AsyncClient, url: str, payload: dict, *, headers: dict | None = None
) -> list[dict]:
    """POST an SSE endpoint and return its events, in order, as dicts."""
    import json

    events: list[dict] = []
    async with client.stream("POST", url, json=payload, headers=headers or {}) as response:
        assert response.status_code == 200, await response.aread()
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    return events


def types_of(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


def first_of(events: list[dict], event_type: str) -> dict | None:
    return next((e for e in events if e["type"] == event_type), None)


def all_of(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e["type"] == event_type]


def answer_text(events: list[dict]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "token")


async def drain_ingestion(timeout_s: float = 30.0) -> None:
    from app.ingestion.worker import drain

    assert await drain(timeout_s), "ingestion queue did not drain in time"
    # One extra beat so the final commit lands before assertions read it.
    await asyncio.sleep(0.2)


WORKSPACE_SCOPE = {"kind": "workspace", "documentIds": []}
