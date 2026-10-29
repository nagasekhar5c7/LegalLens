"""The worker pool. asyncio tasks, bounded at NFR-5's three concurrent
ingestions, each with its own DB session.

The pipeline runs on *this* event loop, not in a nested one. An async SQLAlchemy
engine is bound to the loop that created it, so handing its sessions to
`asyncio.run()` inside a thread is unsound — it happens to work with a cold
connection pool and fails once a pooled connection is reused, which is the
worst possible failure shape: green in a single test, intermittent in
production.

CPU-bound work (pypdf, python-docx, chunking, AES) is what actually needs a
thread, and `pipeline.py` wraps exactly those calls. That keeps a 500-page parse
off the event loop without moving the database off it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_sessionmaker
from app.ingestion import queue
from app.ingestion.pipeline import IngestionFailure, run_pipeline
from app.models import Document, IngestionJob
from app.models.base import utcnow
from app.services import audit

log = logging.getLogger("legallens.ingest.worker")

IDLE_POLL_S = 0.4


class _Pool:
    """Pool state, created per `start_worker()`.

    The stop signal is built here rather than at module import: an
    `asyncio.Event` binds to the first loop that awaits it, so a module-level
    one survives into the next event loop and raises "bound to a different
    event loop" on shutdown. That is invisible in a long-lived server process
    and breaks every test after the first.
    """

    def __init__(self) -> None:
        self.stopping = asyncio.Event()
        self.tasks: list[asyncio.Task[None]] = []


_pool: _Pool | None = None


def worker_status() -> str:
    if _pool is None or not _pool.tasks:
        return "stopped"
    live = sum(1 for t in _pool.tasks if not t.done())
    return f"running ({live}/{len(_pool.tasks)} workers)" if live else "crashed"


async def start_worker() -> None:
    global _pool
    if _pool is not None and _pool.tasks:
        return

    async with get_sessionmaker()() as db:
        await queue.recover_stale(db)
    _sweep_work_dir()

    pool = _Pool()
    n = get_settings().ingest_concurrency
    pool.tasks = [asyncio.create_task(_loop(pool, i), name=f"ingest-{i}") for i in range(n)]
    _pool = pool
    log.info("ingestion worker pool started (%d workers)", n)


async def stop_worker() -> None:
    global _pool
    pool, _pool = _pool, None
    if pool is None:
        return
    pool.stopping.set()
    for task in pool.tasks:
        task.cancel()
    for task in pool.tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _loop(pool: _Pool, index: int) -> None:
    while not pool.stopping.is_set():
        try:
            claimed = await _run_one()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("worker %d crashed on a job; continuing", index)
            claimed = False
        if not claimed:
            try:
                await asyncio.wait_for(pool.stopping.wait(), timeout=IDLE_POLL_S)
            except TimeoutError:
                continue


async def _run_one() -> bool:
    """Returns True when a job was claimed, so the loop knows not to sleep."""
    async with get_sessionmaker()() as db:
        job = await queue.claim_next(db)
        if job is None:
            return False

        document = (
            await db.execute(select(Document).where(Document.id == job.document_id))
        ).scalar_one_or_none()

        if document is None or document.deleted_at is not None:
            # Deleted while queued — FR-26 means that delete wins.
            await queue.fail(db, job, "document was deleted before ingestion ran")
            return True

        try:
            await run_pipeline(db, job, document)
        except IngestionFailure as exc:
            await _mark_failed(db, job.id, document.id, exc.reason, exc.detail, steps=exc.steps)
        except Exception as exc:  # noqa: BLE001 — transient faults get a retry
            log.exception("ingestion job %s failed", job.id)
            job_id, document_id = job.id, document.id
            try:
                retrying = await queue.reschedule(db, job, f"{type(exc).__name__}: {exc}")
            except Exception:
                log.exception("could not reschedule job %s; failing it", job_id)
                retrying = False
            if not retrying:
                await _mark_failed(
                    db,
                    job_id,
                    document_id,
                    "Ingestion failed after repeated attempts",
                    f"The last error was {type(exc).__name__}: {exc}. "
                    "Retry, or re-upload the file.",
                )
        return True


async def _mark_failed(
    failed_db: AsyncSession,
    job_id: str,
    document_id: str,
    reason: str,
    detail: str,
    *,
    steps: list[dict[str, Any]] | None = None,
) -> None:
    """FR-3: a failure is reported on the document, with a next action, and does
    not touch any other file in the batch.

    Uses its own session and re-reads by id. The session that raised may hold a
    broken connection and expired objects, and the one thing that must not fail
    here is recording *why* the document failed — a document stuck at `parsing`
    with no reason is strictly worse than one marked `failed`.
    """
    with contextlib.suppress(Exception):
        await failed_db.rollback()

    async with get_sessionmaker()() as db:
        document = (
            await db.execute(select(Document).where(Document.id == document_id))
        ).scalar_one_or_none()
        job = (
            await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        ).scalar_one_or_none()

        if document is not None:
            document.status = "failed"
            document.progress = None
            document.failure_reason = reason[:500]
            document.failure_detail = detail
            audit.record(
                db,
                action=audit.ACTION_INGEST_FAILED,
                user_id=document.user_id,
                document_ids=[document.id],
                detail={"reason": reason},
            )
        if job is not None:
            job.status = "failed"
            job.step = "failed"
            job.last_error = f"{reason}: {detail}"[:2000]
            job.finished_at = utcnow()
            job.leased_until = None
            if steps:
                # The log as it stood at the failure, so the detail drawer can
                # show exactly which step gave up.
                job.steps_json = steps
        await db.commit()

    log.info("document %s failed ingestion: %s", document_id, reason)


def _sweep_work_dir() -> None:
    """A crashed worker can leave one decrypted file behind. Clearing it at
    startup is the difference between a bounded temp dir and an unbounded one —
    and those files are plaintext privileged material (DR-5)."""
    work = get_settings().data_dir / "work"
    if not work.exists():
        return
    removed = 0
    for path in work.iterdir():
        if path.is_file():
            path.unlink(missing_ok=True)
            removed += 1
    if removed:
        log.warning("swept %d decrypted work file(s) left by a previous process", removed)


async def drain(timeout_s: float = 30.0) -> bool:
    """Test and script helper: wait until the queue is empty."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        async with get_sessionmaker()() as db:
            if await queue.pending_count(db) == 0:
                return True
        await asyncio.sleep(0.1)
    return False
