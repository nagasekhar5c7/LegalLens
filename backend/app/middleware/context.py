"""Request-id and timing — the first two links of the architecture's middleware
chain: `request-id + trace span → ... → audit write`.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger("legallens.access")

Next = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Next) -> Response:
        request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:16]}"
        request.state.request_id = request_id
        started = time.perf_counter()

        response = await call_next(request)

        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-Id"] = request_id
        # Server-Timing shows up in the browser devtools waterfall for free.
        response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"

        # DR-5: path and status only. Never query strings (they can carry a
        # question), never bodies, never document text.
        log.info(
            "%s %s %s %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            extra={"request_id": request_id},
        )
        return response
