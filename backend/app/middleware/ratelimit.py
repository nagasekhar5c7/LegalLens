"""Token-bucket rate limiting, with separate budgets per surface.

One bucket for everything would mean a burst of page reads from the 500-page
viewer could cost a user their ability to ask a question. The viewer polls; the
turn endpoint is expensive. They are not the same budget.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import get_settings
from app.schemas.common import ApiError

Next = Callable[[Request], Awaitable[Response]]


@dataclass
class _Bucket:
    capacity: float
    refill_per_s: float
    tokens: float = field(default=0.0)
    updated: float = field(default_factory=time.monotonic)

    def take(self, n: float = 1.0) -> float | None:
        """Returns None when allowed, else seconds until `n` tokens exist."""
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.refill_per_s)
        self.updated = now
        if self.tokens >= n:
            self.tokens -= n
            return None
        return (n - self.tokens) / self.refill_per_s


def _surface(method: str, path: str) -> str:
    if path.startswith("/api/auth/login"):
        return "login"
    if path.startswith("/api/documents") and method == "POST":
        return "upload"
    if method == "POST" and (
        path.endswith("/messages") or path in ("/api/ask", "/api/summarize", "/api/draft")
    ):
        return "turn"
    return "read"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-process buckets. At NFR-5's ten users this is sufficient and has no
    moving parts; a shared store is a M6 concern alongside multi-instance."""

    def __init__(self, app: object) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        s = get_settings()
        self._limits: dict[str, tuple[float, float]] = {
            "turn": (s.rate_limit_turns_per_min, s.rate_limit_turns_per_min / 60),
            "upload": (s.rate_limit_uploads_per_min, s.rate_limit_uploads_per_min / 60),
            "read": (s.rate_limit_reads_per_min, s.rate_limit_reads_per_min / 60),
            "login": (s.login_attempts_per_min, s.login_attempts_per_min / 60),
        }
        self._buckets: dict[tuple[str, str], _Bucket] = {}

    def _identity(self, request: Request) -> str:
        # Session cookie where there is one, client address otherwise — login
        # throttling has to work before a session exists.
        cookie = request.cookies.get(get_settings().session_cookie_name)
        if cookie:
            return f"sess:{cookie[:32]}"
        client = request.client
        return f"ip:{client.host if client else 'unknown'}"

    async def dispatch(self, request: Request, call_next: Next) -> Response:
        if request.method == "OPTIONS" or not request.url.path.startswith(("/api", "/healthz")):
            return await call_next(request)

        surface = _surface(request.method, request.url.path)
        key = (self._identity(request), surface)
        bucket = self._buckets.get(key)
        if bucket is None:
            capacity, refill = self._limits[surface]
            bucket = _Bucket(capacity=capacity, refill_per_s=refill, tokens=capacity)
            self._buckets[key] = bucket

        wait = bucket.take()
        if wait is None:
            return await call_next(request)

        retry_after = max(1, int(wait + 0.999))
        error = ApiError(
            kind="rate_limited",
            message=f"Too many {surface} requests. This request was not processed.",
            action=f"Retry in {retry_after} seconds.",
            retry_after_sec=retry_after,
            excerpts_available=surface == "turn",
            request_id=getattr(request.state, "request_id", None),
        )
        return JSONResponse(
            status_code=429,
            content=error.model_dump(by_alias=True, exclude_none=True),
            headers={"Retry-After": str(retry_after)},
        )
