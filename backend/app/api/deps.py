"""Dependencies: the DB session, the current user, the scenario override.

Session auth sits in a dependency rather than in middleware so that a route can
be public by simply not asking for it, and so the dependency can share the
request's transaction.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import errors
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import User
from app.services import auth as auth_service

PUBLIC_PATHS = frozenset(
    {"/healthz", "/readyz", "/api/auth/login", "/api/config", "/openapi.json", "/docs", "/redoc"}
)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One transaction per request, committed on a clean exit."""
    async with get_sessionmaker()() as session:
        request.state.db = session
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def current_user(request: Request, db: DbSession) -> User:
    settings = get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        raise errors.unauthorized()
    loaded = await auth_service.load_session(db, session_id)
    if loaded is None:
        raise errors.session_expired()
    session, user = loaded
    await auth_service.touch_session(db, session)
    request.state.user_id = user.id
    request.state.session_id = session.id
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def scenario(request: Request) -> str | None:
    """`X-LegalLens-Scenario` keeps the frontend's Demo menu working against the
    real backend, which is how FR-28 and NFR-6 stay reachable from the UI.

    Dev affordance: ignored outright when ENV=production.
    """
    if not get_settings().scenarios_enabled:
        return None
    value = request.headers.get("x-legallens-scenario")
    if not value:
        return None
    from app.ai.scenarios import is_scenario

    return value if is_scenario(value) else None


Scenario = Annotated[str | None, Depends(scenario)]


async def idempotency_key(request: Request) -> str | None:
    key = request.headers.get("idempotency-key")
    return key[:128] if key else None


IdempotencyKey = Annotated[str | None, Depends(idempotency_key)]


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None
