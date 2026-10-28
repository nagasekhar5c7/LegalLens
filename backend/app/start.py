"""The FastAPI application.

Middleware runs in the architecture's stated order. Starlette applies middleware
in reverse registration order, so the registrations below read bottom-up:
CORS outermost, then request-id, then rate limiting, then the route — where the
session-auth and validation dependencies run, and the per-request transaction
commits the audit row last.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.routes import auth, blobs, conversations, documents, health, platform, turns
from app.api.routes import config as config_routes
from app.config import get_settings
from app.db import dispose_engine, get_engine
from app.logging_setup import configure_logging
from app.middleware.context import RequestContextMiddleware
from app.middleware.ratelimit import RateLimitMiddleware

log = logging.getLogger("legallens")

DESCRIPTION = """
Legal Lens — grounded reading and drafting over an attorney's own documents.

Every output is traceable to a retrieved span, or the system declines. A refusal
is a `200` carrying a structured `refusal` object, not an error: "not answerable
from your documents" is a first-class success state (FR-14, TS-3).

The wire is **camelCase**, matching `frontend/src/api/types.ts`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.require_production_secrets()
    configure_logging()
    settings.blobs_dir.mkdir(parents=True, exist_ok=True)
    get_engine()

    from app.ingestion.worker import start_worker, stop_worker

    await start_worker()
    warming = _start_model_warmup()
    log.info("legallens backend up — env=%s", settings.env)
    try:
        yield
    finally:
        if warming is not None:
            warming.cancel()
        await stop_worker()
        await dispose_engine()


def _start_model_warmup() -> asyncio.Task[None] | None:
    """Load the retrieval models in the background, if this process will use them.

    A background task rather than an `await`: warming takes ~20s and nothing
    about accepting connections depends on it. Health checks, auth and uploads
    all work while it runs, and a turn that arrives early simply blocks on the
    same load it would have paid for anyway.

    Gated on the *resolved* engine, not the configured one. `LL_AI_ENGINE=auto`
    falls back to the fixture engine when there are no credentials, and a
    fixture deployment has no business loading 4.4 GB of weights it will never
    call.
    """
    from app.ai.orchestration.settings import get_router_settings

    if not get_router_settings().warm_models_on_startup:
        log.info("model warm-up disabled — the first turn will pay for the load")
        return None

    # Aliased: `get_engine` at module scope is the *database* engine.
    from app.ai.engine import get_engine as get_ai_engine

    engine = get_ai_engine()
    # Positive check: warm only when we know retrieval will run. An engine
    # added later that does not use the bge stack then costs nothing by
    # default, which is the right way round for a 4.4 GB side effect.
    if not engine.name.startswith("orchestrated"):
        log.info("skipping model warm-up — %s serves this process", engine.name)
        return None

    from app.ai.orchestration.retrieval.factory import warm_models

    return asyncio.create_task(asyncio.to_thread(warm_models))


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Legal Lens API",
        version="0.1.0",
        description=DESCRIPTION,
        openapi_version="3.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,  # the session cookie is the whole auth model
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-LegalLens-Scenario", "X-Request-Id"],
        expose_headers=[
            "X-Request-Id",
            "Retry-After",
            "Server-Timing",
            # The persisted message ids for a streaming turn. Without these the
            # client cannot attach feedback to the turn it just rendered.
            "X-LegalLens-Turn-Id",
            "X-LegalLens-User-Turn-Id",
            "X-LegalLens-Replay",
        ],
    )

    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(config_routes.router)
    app.include_router(auth.router)
    app.include_router(conversations.router)
    # Turns must mount before the generic /api router so that
    # /api/conversations/{id}/messages is matched by its own handler.
    app.include_router(turns.router)
    app.include_router(documents.router)
    app.include_router(blobs.router)
    app.include_router(platform.router)

    return app


app = create_app()
