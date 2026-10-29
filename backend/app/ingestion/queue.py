"""The ingestion queue. A table, not an in-memory list.

FR-3's real requirement is per-file isolation plus durable status: a restart
must not lose the fact that a document was mid-parse, and one file's failure
must not touch the others. A row with a lease gives both. Celery would buy
distribution we do not need at three concurrent ingestions, at the cost of a
broker in everyone's setup.

Claiming is a conditional UPDATE, so two workers cannot take the same job even
without a transaction-level lock.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Document, IngestionJob
from app.models.base import utcnow

log = logging.getLogger("legallens.ingest.queue")


def _rowcount(result: Any) -> int:
    """`rowcount` lives on `CursorResult`; `AsyncSession.execute` is typed as
    returning `Result`. One cast in one place beats an ignore at each call."""
    return int(getattr(result, "rowcount", 0) or 0)


#: Backoff per attempt, in seconds. A parse failure is usually deterministic, so
#: this exists for transient faults (a locked file, a full disk) rather than to
#: paper over a bad document.
BACKOFF_S = (5, 30, 120)


async def enqueue(
    db: AsyncSession, document: Document, *, steps: list[dict[str, Any]] | None = None
) -> IngestionJob:
    job = IngestionJob(
        document_id=document.id,
        status="queued",
        step="queued",
        max_attempts=get_settings().ingest_max_attempts,
        steps_json=steps or [{"label": "Uploaded", "status": "done", "ms": 0}],
        available_at=utcnow(),
    )
    db.add(job)
    document.status = "queued"
    document.progress = 0
    await db.flush()
    return job


async def claim_next(db: AsyncSession) -> IngestionJob | None:
    """Take one runnable job. Returns None when the queue is empty."""
    now = utcnow()
    lease_until = now + timedelta(seconds=get_settings().ingest_visibility_timeout_s)

    candidate = await db.execute(
        select(IngestionJob.id)
        .where(IngestionJob.status == "queued", IngestionJob.available_at <= now)
        .order_by(IngestionJob.available_at, IngestionJob.created_at)
        .limit(1)
    )
    job_id = candidate.scalar_one_or_none()
    if job_id is None:
        return None

    # Conditional update: whoever flips `queued → running` first owns the job.
    taken = await db.execute(
        update(IngestionJob)
        .where(IngestionJob.id == job_id, IngestionJob.status == "queued")
        .values(
            status="running",
            attempt=IngestionJob.attempt + 1,
            leased_until=lease_until,
            started_at=now,
        )
    )
    await db.commit()
    if _rowcount(taken) != 1:
        return None

    result = await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))
    return result.scalar_one_or_none()


async def heartbeat(db: AsyncSession, job: IngestionJob) -> None:
    """Extend the lease. A 500-page parse legitimately outlives one timeout."""
    job.leased_until = utcnow() + timedelta(seconds=get_settings().ingest_visibility_timeout_s)
    await db.commit()


async def reschedule(db: AsyncSession, job: IngestionJob, error: str) -> bool:
    """Retry with backoff. Returns True when a retry was scheduled, False when
    attempts are exhausted and the caller should mark the document failed."""
    job.last_error = error[:2000]
    if job.attempt >= job.max_attempts:
        job.status = "failed"
        job.finished_at = utcnow()
        await db.commit()
        return False

    delay = BACKOFF_S[min(job.attempt - 1, len(BACKOFF_S) - 1)]
    job.status = "queued"
    job.leased_until = None
    job.available_at = utcnow() + timedelta(seconds=delay)
    await db.commit()
    log.info("job %s retrying in %ds (attempt %d)", job.id, delay, job.attempt)
    return True


async def fail(db: AsyncSession, job: IngestionJob, error: str) -> None:
    """Terminal failure — no retry. A password-protected PDF will not become
    readable on the second attempt."""
    job.status = "failed"
    job.step = "failed"
    job.last_error = error[:2000]
    job.leased_until = None
    job.finished_at = utcnow()
    await db.commit()


async def recover_stale(db: AsyncSession) -> int:
    """Startup recovery: a job whose lease expired belonged to a process that is
    no longer running. Without this, a crash during ingestion leaves a document
    stuck at `parsing` forever."""
    now = utcnow()
    result = await db.execute(
        update(IngestionJob)
        .where(
            IngestionJob.status == "running",
            IngestionJob.leased_until.is_not(None),
            IngestionJob.leased_until < now,
        )
        .values(status="queued", leased_until=None, available_at=now)
    )
    await db.commit()
    count = _rowcount(result)
    if count:
        log.warning("recovered %d stale ingestion job(s) after restart", count)
    return count


async def pending_count(db: AsyncSession) -> int:
    result = await db.execute(
        select(IngestionJob.id).where(IngestionJob.status.in_(("queued", "running")))
    )
    return len(result.scalars().all())
