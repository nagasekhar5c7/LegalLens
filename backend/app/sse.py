"""SSE framing.

Two things here are not obvious and both are load-bearing:

  * **The discriminator is inside the JSON as well as on the `event:` line.**
    `types.ts` discriminates `StreamEvent` on `type`, and a POST body cannot be
    read by `EventSource` anyway — the frontend uses `fetch` + `ReadableStream`.
    Emitting both costs one line and keeps either consumer working.
  * **Keep-alive comments.** A summary is budgeted at three minutes (NFR-3).
    Without a periodic `:` comment, an idle-connection reaper between the client
    and the app closes the stream and the user sees a hang.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.config import get_settings
from app.schemas.events import StreamEvent

log = logging.getLogger("legallens.sse")

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # nginx buffers `text/event-stream` by default, which turns a token stream
    # into one delivery at the end. This is the header that stops it.
    "X-Accel-Buffering": "no",
}

MEDIA_TYPE = "text/event-stream"


def frame(event: StreamEvent) -> str:
    # `exclude_none` would be wrong here. `SummaryField.valueMd` is declared
    # `string | null` in types.ts and the null is load-bearing: it is how FR-16
    # distinguishes "not addressed in this document" from a field that was never
    # sent. Dropping it would turn an honest absence into a missing key.
    payload = event.model_dump(by_alias=True)
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return f"event: {event.type}\ndata: {data}\n\n"


def comment(text: str) -> str:
    return f": {text}\n\n"


def retry_hint(ms: int = 3000) -> str:
    return f"retry: {ms}\n\n"


async def with_keepalive(
    events: AsyncIterator[StreamEvent],
    *,
    on_event: Callable[[StreamEvent], Awaitable[None]] | None = None,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[str]:
    """Wrap an event stream in SSE frames, injecting keep-alives during gaps.

    `on_event` is awaited for each event before it is yielded, which is how the
    route accumulates the turn it will persist. `is_disconnected` lets the route
    stop generating when the client has gone — NFR-7's "interrupted stream is
    marked incomplete" depends on noticing.
    """
    interval = get_settings().sse_keepalive_s
    yield retry_hint()

    iterator = events.__aiter__()
    pending: asyncio.Task[StreamEvent] | None = None
    try:
        while True:
            if is_disconnected is not None and await is_disconnected():
                log.info("client disconnected mid-stream")
                return

            if pending is None:
                pending = asyncio.ensure_future(_anext(iterator))

            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield comment("keep-alive")
                continue

            task, pending = pending, None
            try:
                event = task.result()
            except StopAsyncIteration:
                return

            if on_event is not None:
                await on_event(event)
            yield frame(event)
    finally:
        if pending is not None and not pending.done():
            pending.cancel()


async def _anext(iterator: AsyncIterator[StreamEvent]) -> StreamEvent:
    return await iterator.__anext__()


def parse_stream(body: str) -> list[dict[str, Any]]:
    """Test helper: SSE text back into event payloads, in order."""
    events: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    return events
