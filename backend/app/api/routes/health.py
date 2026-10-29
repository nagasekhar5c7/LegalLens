"""Liveness and readiness. NFR-6: readiness reports a degraded dependency
rather than failing, because retrieval is useful during an LLM outage."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.db import get_sessionmaker

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict[str, Any]:
    checks: dict[str, str] = {}
    try:
        async with get_sessionmaker()() as db:
            await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 — readiness reports, never raises
        checks["database"] = f"error: {type(exc).__name__}"

    from app.ingestion.worker import worker_status

    checks["ingestion_worker"] = worker_status()

    # NFR-6 again, for the ingestion half: a backend with no OCR engine and no
    # vector store is *degraded*, not broken — documents still parse, pages and
    # spans are still real, and the library still works. But "why does search
    # return nothing" has to be answerable before an upload rather than after
    # one, so the resolved engines are reported here and not only in the logs.
    ingestion: dict[str, Any] = {}
    try:
        from app.config import get_settings
        from app.indexing import describe as describe_index
        from app.ingestion.ocr import describe as describe_ocr

        settings = get_settings()
        ingestion["ocr"] = describe_ocr(
            settings.ocr_engine, dpi=settings.ocr_dpi, languages=settings.ocr_languages
        )
        ingestion["index"] = describe_index()
    except Exception as exc:  # noqa: BLE001 — readiness reports, never raises
        ingestion["error"] = f"{type(exc).__name__}: {exc}"

    # The generation half of the same argument. `LL_AI_ENGINE=auto` on a
    # checkout with no API key starts, serves, and answers every question from
    # a scripted fixture — which is indistinguishable from working unless
    # something says so. `generation.grounded` is that something.
    generation: dict[str, Any] = {}
    try:
        from app.ai.engine import describe as describe_engine

        generation = describe_engine()
    except Exception as exc:  # noqa: BLE001 — readiness reports, never raises
        generation["error"] = f"{type(exc).__name__}: {exc}"

    ready = checks["database"] == "ok"
    return {
        "status": "ready" if ready else "degraded",
        "checks": checks,
        "ingestion": ingestion,
        "generation": generation,
    }
