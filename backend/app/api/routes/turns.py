"""The chat turn endpoints. FR-10: answers stream token-by-token over SSE.

Four routes, one code path:

    POST /api/conversations/{id}/messages   mode from the body, router decides
    POST /api/ask | /api/summarize | /api/draft   explicit mode, router skipped

The explicit-mode endpoints exist because the architecture says so (BRD §7) and
because an attorney who picked "Summary" should not have an LLM second-guess
them. They run the same workflow underneath.

Lifecycle note worth knowing before editing this file: a `StreamingResponse`
generator runs after the route function returns, so the request-scoped session
from `get_db` is closed by then. All pre-stream work commits here; everything
during and after the stream uses a session opened inside the generator.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.ai.engine import get_engine
from app.ai.ports import AiEngine, AiRequest, AiUnavailable
from app.api import errors
from app.api.deps import CurrentUser, DbSession, IdempotencyKey, Scenario, client_ip
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Conversation
from app.schemas.common import ApiError
from app.schemas.conversations import ModeRequest, SendMessageRequest
from app.schemas.events import ErrorEvent, StreamEvent
from app.services import audit
from app.services import conversations as conv_service
from app.services import scope as scope_service
from app.services import turns as turn_service
from app.sse import MEDIA_TYPE, SSE_HEADERS, with_keepalive

log = logging.getLogger("legallens.turns.api")

router = APIRouter(prefix="/api", tags=["turns"])

#: Strong references to in-flight turn writes.
#:
#: The event loop holds only weak references to tasks, so a detached
#: `_finalise` can be garbage-collected part-way through writing the turn it is
#: meant to preserve. See the `finally` in `_stream_turn.body_stream`.
_PERSISTING: set[asyncio.Task[None]] = set()

# The engine is resolved through `app.ai.engine.get_engine()`, so this module
# never names an implementation. See that file for the M4 swap point.


# --------------------------------------------------------------- the routes


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=None,
    responses={200: {"content": {"text/event-stream": {}}, "description": "SSE event stream"}},
)
async def send_message(
    conversation_id: str,
    body: SendMessageRequest,
    request: Request,
    user: CurrentUser,
    db: DbSession,
    scenario: Scenario,
    idempotency_key: IdempotencyKey,
) -> StreamingResponse | JSONResponse:
    conversation = await conv_service.get_conversation(db, user.id, conversation_id)
    if conversation is None:
        raise errors.not_found("That conversation")
    return await _stream_turn(
        request, db, user.id, conversation, body, None, scenario, idempotency_key
    )


@router.post(
    "/ask",
    response_model=None,
    responses={200: {"content": {"text/event-stream": {}}, "description": "SSE event stream"}},
)
async def ask(
    body: ModeRequest,
    request: Request,
    user: CurrentUser,
    db: DbSession,
    scenario: Scenario,
    idempotency_key: IdempotencyKey,
) -> StreamingResponse | JSONResponse:
    return await _mode_endpoint(request, db, user.id, body, "ask", scenario, idempotency_key)


@router.post(
    "/summarize",
    response_model=None,
    responses={200: {"content": {"text/event-stream": {}}, "description": "SSE event stream"}},
)
async def summarize(
    body: ModeRequest,
    request: Request,
    user: CurrentUser,
    db: DbSession,
    scenario: Scenario,
    idempotency_key: IdempotencyKey,
) -> StreamingResponse | JSONResponse:
    return await _mode_endpoint(request, db, user.id, body, "summary", scenario, idempotency_key)


@router.post(
    "/draft",
    response_model=None,
    responses={200: {"content": {"text/event-stream": {}}, "description": "SSE event stream"}},
)
async def draft(
    body: ModeRequest,
    request: Request,
    user: CurrentUser,
    db: DbSession,
    scenario: Scenario,
    idempotency_key: IdempotencyKey,
) -> StreamingResponse | JSONResponse:
    return await _mode_endpoint(request, db, user.id, body, "draft", scenario, idempotency_key)


async def _mode_endpoint(
    request: Request,
    db: DbSession,
    user_id: str,
    body: ModeRequest,
    forced_intent: str,
    scenario: str | None,
    idempotency_key: str | None,
) -> StreamingResponse | JSONResponse:
    """`conversationId` is optional on these, so `/api/ask` works standalone —
    which is what makes M0's canned-response contract testable with one curl."""
    # Check for a replay *before* creating anything: otherwise a retried
    # /api/ask opens a new conversation every time and never finds its own
    # stored turn.
    if idempotency_key:
        replay = await turn_service.find_replay(db, user_id, idempotency_key)
        if replay is not None:
            log.info("idempotent replay of %s", replay.id)
            return JSONResponse(
                content=conv_service.to_turn(replay).model_dump(by_alias=True, mode="json"),
                headers={"X-LegalLens-Replay": "true"},
            )

    conversation: Conversation | None = None
    if body.conversation_id:
        conversation = await conv_service.get_conversation(db, user_id, body.conversation_id)
        if conversation is None:
            raise errors.not_found("That conversation")
    else:
        conversation = await conv_service.create(db, user_id)

    # The chip the user pressed wins over the router (frontend nuance #4).
    body = body.model_copy(update={"mode": forced_intent})
    return await _stream_turn(
        request, db, user_id, conversation, body, forced_intent, scenario, idempotency_key
    )


# ------------------------------------------------------------- the machinery


async def _stream_turn(
    request: Request,
    db: DbSession,
    user_id: str,
    conversation: Conversation,
    body: SendMessageRequest,
    forced_intent: str | None,
    scenario: str | None,
    idempotency_key: str | None,
) -> StreamingResponse | JSONResponse:
    settings = get_settings()

    # Idempotent replay: return the stored turn rather than generating (and
    # charging for) a second one.
    if idempotency_key:
        replay = await turn_service.find_replay(db, user_id, idempotency_key)
        if replay is not None:
            log.info("idempotent replay of %s", replay.id)
            return JSONResponse(
                content=conv_service.to_turn(replay).model_dump(by_alias=True, mode="json"),
                headers={"X-LegalLens-Replay": "true"},
            )

    # FR-19: a draft with no sources cannot be grounded, so refuse the request
    # rather than produce an ungrounded draft.
    if forced_intent == "draft" or body.mode == "draft":
        params = body.draft_params
        if params is None or not params.document_ids:
            audit.record(
                db,
                action=audit.ACTION_POLICY_REFUSE,
                user_id=user_id,
                conversation_id=conversation.id,
                detail={"reason": "draft requested with no source documents"},
                ip=client_ip(request),
            )
            await db.commit()
            raise errors.bad_request(
                "A draft needs at least one source document. Without sources there is "
                "nothing to ground it in, and an ungrounded draft is the failure mode "
                "this product exists to avoid.",
                "Select the documents the draft should be built from, then try again.",
            )

    scope_used, scoped_documents = await scope_service.resolve(db, user_id, body.scope)
    history = await turn_service.recent_history(db, conversation.id)
    trace_id = turn_service.new_trace_id()

    # Both rows exist and are committed before a single byte streams: if the
    # process dies mid-generation, the thread still shows the question asked
    # and an assistant turn marked `streaming` that startup reconciles.
    user_turn = await turn_service.persist_user_turn(
        db, conversation, body, scope_used, idempotency_key=idempotency_key
    )
    assistant = await turn_service.create_assistant_turn(db, conversation, body, trace_id)
    await conv_service.touch(db, conversation)
    assistant_id = assistant.id
    user_turn_id = user_turn.id
    await db.commit()

    ai_request = turn_service.build_ai_request(
        body,
        scope_used=scope_used,
        documents=scoped_documents,
        conversation_id=conversation.id,
        trace_id=trace_id,
        user_id=user_id,
        history=history,
        scenario=scenario,
    )

    accumulator = turn_service.TurnAccumulator(requested_mode=body.mode)

    async def events() -> AsyncIterator[StreamEvent]:
        """The engine call, under the generation budget."""
        try:
            async with asyncio.timeout(settings.generation_budget_s):
                iterator = _dispatch(get_engine(), ai_request, forced_intent)
                async for event in iterator:
                    yield event
        except TimeoutError:
            # FR-28 / NFR-7: name a next action, and do not present the partial
            # text as complete. `final_status` turns this into `incomplete`.
            log.warning("generation budget exceeded for %s", assistant_id)
            yield ErrorEvent(
                error=ApiError(
                    kind="timeout",
                    message=(
                        f"Generation exceeded the {settings.generation_budget_s}-second budget "
                        "and was abandoned."
                    ),
                    action="Retry with a narrower scope, or ask about one document at a time.",
                    excerpts_available=bool(accumulator.excerpts),
                )
            )
        except AiUnavailable as exc:
            # NFR-6: retrieval already ran, so the excerpts are still worth
            # reading and the error says so.
            log.warning("AI layer unavailable: %s", exc)
            yield ErrorEvent(
                error=ApiError(
                    kind="llm_unavailable",
                    message=str(exc),
                    action=(
                        "Retry, or read the retrieved passages directly — they are listed "
                        "in the evidence panel."
                    ),
                    retry_after_sec=exc.retry_after_sec,
                    excerpts_available=bool(accumulator.excerpts),
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("engine failed for %s", assistant_id)
            yield ErrorEvent(
                error=ApiError(
                    kind="llm_unavailable",
                    message="The answer could not be generated.",
                    action="Retry. Retrieval succeeded, so the retrieved passages are available.",
                    excerpts_available=bool(accumulator.excerpts),
                )
            )

    async def body_stream() -> AsyncIterator[str]:
        disconnected = False
        try:
            async for chunk in with_keepalive(
                events(),
                on_event=_observe(accumulator),
                is_disconnected=request.is_disconnected,
            ):
                yield chunk
            disconnected = not accumulator.done and accumulator.error is None
        except asyncio.CancelledError:
            # The client went away mid-stream. The turn is still persisted, as
            # `incomplete` — NFR-7 is about what the record says, not only
            # about what the UI paints.
            disconnected = True
            raise
        finally:
            # Shielded, and this is the whole reason the turn gets recorded at
            # all when the user presses Stop.
            #
            # A plain `await` here is unreachable under cancellation: Starlette
            # cancels this task the moment it notices the disconnect, so the
            # first await inside the `finally` re-raises `CancelledError` and
            # `_finalise` never reaches its session. The turn was then left
            # `streaming` for ever — a row claiming to be in progress for a
            # generation that stopped, which is precisely what NFR-7 forbids.
            #
            # `shield` detaches the write so the cancellation cannot reach it,
            # and the task is held in a module-level set because the loop only
            # keeps weak references to tasks and would otherwise be free to
            # collect this one mid-write. The `await` is kept for the ordinary
            # path, where the response must not finish before the turn is
            # durable — a client that reads `done` and immediately re-reads the
            # conversation has to see it.
            task = asyncio.create_task(
                _finalise(assistant_id, accumulator, ai_request, user_id, disconnected=disconnected)
            )
            _PERSISTING.add(task)
            task.add_done_callback(_PERSISTING.discard)
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(task)

    # Both ids are known before the first byte, and the client needs them: it
    # renders optimistically with ids of its own making, so without these
    # `POST /api/feedback` would carry an id this server has never seen. Headers
    # rather than a new event variant — they are facts about the response, not
    # something that happens during it. Both are in the CORS expose list.
    return StreamingResponse(
        body_stream(),
        media_type=MEDIA_TYPE,
        headers={
            **SSE_HEADERS,
            "X-LegalLens-Turn-Id": assistant_id,
            "X-LegalLens-User-Turn-Id": user_turn_id,
        },
    )


async def _dispatch(
    engine: AiEngine, request: AiRequest, forced_intent: str | None
) -> AsyncIterator[StreamEvent]:
    """Explicit mode goes straight to its workflow; `auto` routes first.

    An async generator rather than a function returning one, because routing is
    an `await` now: the orchestrated engine classifies with a model call, and
    the port went async with it. The routing decision is left on
    `request.trace` by the engine, which is how `answer()` learns the rationale
    and the `out_of_scope` verdict without paying for a second classification.
    """
    if forced_intent == "ask":
        stream = engine.answer(request)
    elif forced_intent == "summary":
        stream = engine.summarize(request)
    elif forced_intent == "draft":
        stream = engine.draft(request)
    else:
        intent = await engine.route_intent(request)
        if intent == "summary":
            stream = engine.summarize(request)
        elif intent == "draft":
            stream = engine.draft(request)
        else:
            # `out_of_scope` is handled inside the ask flow, which refuses with
            # the right message rather than erroring.
            stream = engine.answer(request)

    async for event in stream:
        yield event


def _observe(
    accumulator: turn_service.TurnAccumulator,
) -> Callable[[StreamEvent], Awaitable[None]]:
    async def observe(event: StreamEvent) -> None:
        accumulator.observe(event)

    return observe


async def _finalise(
    assistant_id: str,
    accumulator: turn_service.TurnAccumulator,
    ai_request: AiRequest,
    user_id: str,
    *,
    disconnected: bool,
) -> None:
    """Own session: the request's is long closed by the time this runs."""
    try:
        async with get_sessionmaker()() as db:
            status_written = await turn_service.finalise(
                db,
                message_id=assistant_id,
                accumulator=accumulator,
                request=ai_request,
                user_id=user_id,
                engine_name=get_engine().name,
                disconnected=disconnected,
                queries=_queries_from(accumulator, ai_request),
                candidates=_candidates_from(accumulator, ai_request),
            )
        log.info("turn %s persisted as %s", assistant_id, status_written)
    except Exception:
        # A failure here loses the record of a turn the user already saw, so it
        # is logged loudly rather than swallowed.
        log.exception("failed to persist turn %s", assistant_id)


def _queries_from(accumulator: turn_service.TurnAccumulator, request: AiRequest) -> list[str]:
    """NFR-11: the queries actually run.

    The engine reports them on `request.trace` — for the ask workflow that is
    the *rewritten* query, which is the one retrieval saw and which no stream
    event carries. The two fallbacks below are for an engine that reports
    nothing: a refusal states what it looked for, and the stage details are the
    last resort.
    """
    if request.trace.queries:
        return list(request.trace.queries)
    if accumulator.refusal:
        return list(accumulator.refusal.get("looked_for", []))
    return [s["detail"] for s in accumulator.stages if s.get("detail")]


def _candidates_from(
    accumulator: turn_service.TurnAccumulator, request: AiRequest
) -> list[dict[str, Any]]:
    """The candidate set with scores, so a retrieval failure is diagnosable
    from the trace without re-running the query.

    The engine's own list is preferred because it includes the candidates that
    were *rejected*, and those are the diagnostically useful ones — an excerpt
    list can only ever show what survived the floor. Falling back to excerpts
    keeps a fixture-engine turn traceable.
    """
    if request.trace.candidates:
        return list(request.trace.candidates)
    return [
        {
            "span_id": e["span_id"],
            "document_id": e["document_id"],
            "page": e["page"],
            "score": e["relevance"],
            "kept": True,
        }
        for e in accumulator.excerpts
    ]


@router.get("/turns/{turn_id}", status_code=status.HTTP_200_OK, response_model=None)
async def get_turn(turn_id: str, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """⊕ Re-read one turn. The frontend uses this after a reconnect to find out
    whether a stream it lost actually completed — otherwise an interrupted
    stream and a completed-but-unseen one are indistinguishable."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models import Message

    message = (
        await db.execute(
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Message.id == turn_id, Conversation.user_id == user.id)
            .options(selectinload(Message.citations), selectinload(Message.feedback))
        )
    ).scalar_one_or_none()
    if message is None:
        raise errors.not_found("That turn")
    return conv_service.to_turn(message).model_dump(by_alias=True, mode="json")
