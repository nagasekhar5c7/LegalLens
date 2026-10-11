"""The FastAPI application.

Middleware runs in the architecture's stated order. Starlette applies middleware
in reverse registration order, so the registrations below read bottom-up:
CORS outermost, then request-id, then rate limiting, then the route — where the
session-auth and validation dependencies run, and the per-request transaction
commits the audit row last.
"""

from __future__ import annotations

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
    log.info("legallens backend up — env=%s", settings.env)
    try:
        yield
    finally:
        await stop_worker()
        await dispose_engine()


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
