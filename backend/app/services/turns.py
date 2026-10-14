"""Turn persistence: accumulating a stream into a durable record.

The subtle constraint here is lifecycle. A `StreamingResponse` generator runs
*after* the route function returns, so the request-scoped DB session is already
closed by the time the last token is emitted. Everything the stream persists
therefore uses its own session, opened inside the generator. Using the
request's session would work in tests and fail under load with
"operation on closed connection" — the kind of bug that only shows up in
staging.

What gets written, per the architecture's "(6) PERSIST + AUDIT":

  * the message row (user, then assistant)
  * citation rows → span refs
  * a retrieval trace: queries, candidate ids, scores
  * a usage / cost transaction
  * an audit row (DR-4)

all in one transaction, so a turn is either recorded or it is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.disclaimers import INCOMPLETE_MARKING
from app.ai.ports import AiRequest
from app.models import Conversation, Message, MessageCitation, Trace, UsageTransaction
from app.models.base import prefixed_id
from app.schemas.common import Scope, ScopeUsed
from app.schemas.conversations import SendMessageRequest
from app.schemas.events import StreamEvent
from app.services import audit
from app.services import conversations as conv_service

log = logging.getLogger("legallens.turns")


@dataclass
class TurnAccumulator:
    """Collects a stream into the envelope that will be persisted and replayed.

    It never decides anything — it only records what the engine emitted. The
    status rules live in `final_status`, where they can be read in one place.
    """

    requested_mode: str
    intent: str | None = None
    kind: str = "answer"
    text_parts: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    excerpts: list[dict[str, Any]] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    summary_fields: list[dict[str, Any]] = field(default_factory=list)
    draft_sections: list[dict[str, Any]] = field(default_factory=list)
    scope_used: dict[str, Any] | None = None
    refusal: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    coverage: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    versions: dict[str, Any] | None = None
    trace_id: str | None = None
    disclaimer: str | None = None
    done: bool = False
    summary_meta: dict[str, Any] | None = None
    draft_meta: dict[str, Any] | None = None

    def observe(self, event: StreamEvent) -> None:
        payload = event.model_dump(by_alias=False, exclude_none=True)
        kind = event.type

        if kind == "token":
            self.text_parts.append(payload["text"])
        elif kind == "intent":
            self.intent = payload["intent"]
            if self.intent in ("summary", "draft"):
                self.kind = self.intent
        elif kind == "scope":
            self.scope_used = payload["scope_used"]
        elif kind == "citation":
            self.citations.append(payload["citation"])
        elif kind == "excerpt":
            self.excerpts.append(payload["excerpt"])
        elif kind == "stage":
            stage = payload["stage"]
            # Replace the active row rather than appending both, so the
            # persisted trace reads as a sequence of finished stages.
            for i, existing in enumerate(self.stages):
                if existing["key"] == stage["key"] and existing["status"] == "active":
                    self.stages[i] = stage
                    break
            else:
                self.stages.append(stage)
        elif kind == "summary_start":
            self.summary_meta = payload["summary"]
            self.kind = "summary"
        elif kind == "summary_field":
            self.summary_fields.append(payload["field"])
            self.kind = "summary"
        elif kind == "draft_start":
            self.draft_meta = payload["draft"]
            self.kind = "draft"
        elif kind == "draft_section":
            self.draft_sections.append(payload["section"])
            self.kind = "draft"
        elif kind == "refusal":
            self.refusal = payload["refusal"]
        elif kind == "error":
            self.error = payload["error"]
        elif kind == "coverage":
            self.coverage = payload["coverage"]
        elif kind == "usage":
            self.usage = payload["usage"]
            self.versions = payload["versions"]
            self.trace_id = payload["trace_id"]
        elif kind == "done":
            self.disclaimer = payload["disclaimer"]
            self.done = True

    # ------------------------------------------------------------- outcome

    @property
    def text(self) -> str:
        return "".join(self.text_parts)

    def final_status(self, *, disconnected: bool = False) -> str:
        """NFR-7: no partial output is presented as complete.

        Order matters. A stream that errored mid-answer having already emitted
        tokens is `incomplete`, not `error` — the user is looking at partial
        text and the record has to say so. An error with nothing emitted is a
        plain `error`.
        """
        has_content = bool(self.text_parts or self.summary_fields or self.draft_sections)
        if self.error is not None:
            return "incomplete" if has_content else "error"
        if disconnected or not self.done:
            return "incomplete" if has_content else "error"
        if self.refusal is not None:
            return "refused"
        return "complete"

    def envelope(self, request: AiRequest) -> dict[str, Any]:
        """Everything needed to re-render the turn, minus the citations — those
        live in `message_citations`, which is what the FR-21 / NFR-15 precision
        query reads."""
        summary = None
        if self.summary_fields or self.kind == "summary":
            meta = self.summary_meta or {}
            summary = {
                "document_id": meta.get("document_id", ""),
                "document_name": meta.get("document_name", ""),
                "document_type": meta.get("document_type", "other"),
                "template_name": meta.get("template_name", ""),
                "fields": self.summary_fields,
                "coverage": self.coverage or meta.get("coverage"),
            }

        draft = None
        if self.draft_sections or self.kind == "draft":
            params = (
                request.draft_params.model_dump()
                if request.draft_params is not None
                else {"draft_type": "memo", "document_ids": list(request.scope_used.document_ids)}
            )
            meta = self.draft_meta or {}
            draft = {
                "draft_type": meta.get("draft_type") or params.get("draft_type", "memo"),
                "title": meta.get("title", ""),
                "params": meta.get("params") or params,
                "sections": self.draft_sections,
            }

        return {
            "excerpts": self.excerpts,
            "stages": self.stages,
            "scope_used": self.scope_used,
            "summary": summary,
            "draft": draft,
            "refusal": self.refusal,
            "error": self.error,
            "coverage": self.coverage,
            "usage": self.usage,
            "versions": self.versions,
            "disclaimer": self.disclaimer,
        }

    def display_text(self, status: str) -> str:
        """FR-24 / NFR-7: the incomplete marking is part of the text, so it
        survives the clipboard and the Markdown export."""
        text = self.text
        if status == "incomplete" and text:
            return f"{text}\n\n> **{INCOMPLETE_MARKING}**"
        return text


# ----------------------------------------------------------------- writing


async def persist_user_turn(
    db: AsyncSession,
    conversation: Conversation,
    body: SendMessageRequest,
    scope_used: ScopeUsed,
    *,
    idempotency_key: str | None = None,
) -> Message:
    """Written before the stream starts: if generation fails, the question the
    user asked is still in the thread."""
    seq = await conv_service.next_seq(db, conversation.id)
    message = Message(
        conversation_id=conversation.id,
        seq=seq,
        role="user",
        text=body.text,
        mode=body.mode,
        scope_json=body.scope.model_dump(),
        draft_params_json=body.draft_params.model_dump() if body.draft_params else None,
        idempotency_key=idempotency_key,
    )
    db.add(message)
    await conv_service.autotitle(db, conversation, body.text)
    await db.flush()
    return message


async def create_assistant_turn(
    db: AsyncSession, conversation: Conversation, body: SendMessageRequest, trace_id: str
) -> Message:
    """Created up front with `status='streaming'`, so a crash mid-stream leaves
    a row that says so rather than no row at all."""
    seq = await conv_service.next_seq(db, conversation.id)
    message = Message(
        conversation_id=conversation.id,
        seq=seq,
        role="assistant",
        text="",
        requested_mode=body.mode,
        status="streaming",
        kind="answer",
        trace_id=trace_id,
    )
    db.add(message)
    await db.flush()
    return message


async def finalise(
    db: AsyncSession,
    *,
    message_id: str,
    accumulator: TurnAccumulator,
    request: AiRequest,
    user_id: str,
    engine_name: str,
    disconnected: bool = False,
    queries: list[str] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> str:
    """One transaction: message, citations, trace, usage, audit."""
    message = (
        await db.execute(select(Message).where(Message.id == message_id))
    ).scalar_one_or_none()
    if message is None:
        log.error("assistant message %s vanished before finalisation", message_id)
        return "error"

    status = accumulator.final_status(disconnected=disconnected)
    message.status = status
    message.kind = accumulator.kind
    message.intent = accumulator.intent
    message.text = accumulator.display_text(status)
    message.envelope_json = accumulator.envelope(request)
    message.trace_id = accumulator.trace_id or message.trace_id

    for citation in accumulator.citations:
        db.add(
            MessageCitation(
                message_id=message.id,
                n=citation["n"],
                span_id=citation["span_id"],
                document_id=citation["document_id"],
                document_name=citation["document_name"],
                page=citation["page"],
                section=citation.get("section"),
                label=citation["label"],
                # The engine resolved every citation it emitted against the
                # span map. M4's gate sets this from a real verification pass.
                resolved=True,
            )
        )

    versions = accumulator.versions or {}
    if message.trace_id:
        db.add(
            Trace(
                id=message.trace_id,
                user_id=user_id,
                message_id=message.id,
                conversation_id=message.conversation_id,
                stages_json=accumulator.stages,
                queries_json=queries or [],
                candidates_json=candidates or [],
                timings_json={
                    "latency_ms": (accumulator.usage or {}).get("latency_ms"),
                    "ttft_ms": (accumulator.usage or {}).get("time_to_first_token_ms"),
                    "engine": engine_name,
                    # NFR-11 per stage, and NFR-8 per call. The `usage` event
                    # carries the turn's total; `calls` says where it went,
                    # which a turn spending on Haiku for routing, Haiku for the
                    # rewrite and Sonnet for generation needs in order to be
                    # attributable at all. `detail` holds the routing decision,
                    # the rewrite and what the citation gate did.
                    "stages_ms": dict(request.trace.timings_ms) or None,
                    "calls": list(request.trace.calls) or None,
                    "detail": dict(request.trace.detail) or None,
                },
                versions_json=versions or None,
            )
        )

    if accumulator.usage:
        usage = accumulator.usage
        db.add(
            UsageTransaction(
                user_id=user_id,
                conversation_id=message.conversation_id,
                message_id=message.id,
                kind="turn",
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                usd=float(usage.get("usd", 0.0)),
                latency_ms=float(usage.get("latency_ms", 0.0)),
                ttft_ms=usage.get("time_to_first_token_ms"),
                model=versions.get("model"),
            )
        )

    # NFR-8 wants spend attributable per request, and a turn is no longer one
    # call: routing and the query rewrite run on Haiku, generation on Sonnet,
    # and a nine-field summary runs nine generation calls. The `kind="turn"` row
    # above stays as the turn's total — reports and budget checks read it, and
    # changing what it means would silently double every historical figure —
    # and these rows sit beside it saying where it went.
    #
    # Grouped by phase rather than one row per call, for two reasons.
    # `UsageTransaction.kind` is `String(16)`, which `call:summary:liability`
    # does not fit; and a row per call puts ten rows on every summary turn for
    # a breakdown nobody aggregates at that grain. The per-field detail is on
    # the trace, which is where it is actually read.
    for kind, grouped in _group_calls(request.trace.calls).items():
        db.add(
            UsageTransaction(
                user_id=user_id,
                conversation_id=message.conversation_id,
                message_id=message.id,
                kind=kind,
                input_tokens=grouped["input_tokens"],
                output_tokens=grouped["output_tokens"],
                usd=grouped["usd"],
                latency_ms=grouped["latency_ms"],
                model=grouped["model"],
            )
        )

    # DR-4: document IDs, retrieved spans, prompt/model/index version, output
    # hash, timestamp, user — written with the message, not after it.
    audit.record(
        db,
        action=audit.ACTION_REFUSE if status == "refused" else audit.ACTION_GENERATE,
        user_id=user_id,
        conversation_id=message.conversation_id,
        message_id=message.id,
        document_ids=list(request.scope_used.document_ids),
        span_ids=[c["span_id"] for c in accumulator.citations],
        prompt_version=versions.get("prompt"),
        model_version=versions.get("model"),
        index_version=versions.get("index"),
        trace_id=message.trace_id,
        output=accumulator.text or None,
        detail={
            "status": status,
            "intent": accumulator.intent,
            "kind": accumulator.kind,
            "citations": len(accumulator.citations),
            "excerpts": len(accumulator.excerpts),
            "scope_kind": request.scope_used.kind,
            "excluded": len(request.scope_used.excluded),
            "engine": engine_name,
        },
    )

    conversation = (
        await db.execute(select(Conversation).where(Conversation.id == message.conversation_id))
    ).scalar_one_or_none()
    if conversation is not None:
        await conv_service.touch(db, conversation, kind=accumulator.kind)

    await db.commit()
    return status


#: Trace stage -> `UsageTransaction.kind`. Every value is <= 16 characters,
#: which is the column's width; an unrecognised stage is bucketed rather than
#: truncated, because a silently clipped kind would split one phase's spend
#: across two labels in every report that groups by it.
_USAGE_KIND: dict[str, str] = {
    "routing": "routing",
    "rewrite": "rewrite",
    "generate": "generate",
}


def _group_calls(calls: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-call usage, summed per phase.

    The model recorded is the first one seen for that phase. Within a phase it
    is the same model by construction — all nine summary fields run on
    `summary_model` — so this is a fact rather than an approximation.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for call in calls:
        stage = str(call.get("stage", "")).split(":", 1)[0]
        kind = _USAGE_KIND.get(stage, "generate")
        row = grouped.setdefault(
            kind,
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "usd": 0.0,
                "latency_ms": 0.0,
                "model": call.get("model"),
            },
        )
        row["input_tokens"] += int(call.get("input_tokens", 0))
        row["output_tokens"] += int(call.get("output_tokens", 0))
        row["usd"] += float(call.get("usd", 0.0))
        row["latency_ms"] += float(call.get("latency_ms", 0.0))
    for row in grouped.values():
        row["usd"] = round(row["usd"], 6)
    return grouped


async def find_replay(db: AsyncSession, user_id: str, idempotency_key: str) -> Message | None:
    """A replayed POST returns the stored turn rather than generating a second
    one. Double-submit is common — an impatient click, a retried fetch — and
    paying twice for it is both a cost and a correctness problem.

    Scoped to the *user*, not to a conversation: `/api/ask` creates a
    conversation when none is given, so a conversation-scoped lookup would
    never match its own replay. The key is client-generated per attempt, so
    per-user is both sufficient and correct.
    """
    from sqlalchemy.orm import selectinload

    user_message = (
        (
            await db.execute(
                select(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    Conversation.user_id == user_id,
                    Message.idempotency_key == idempotency_key,
                    Message.role == "user",
                )
                .order_by(Message.created_at)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if user_message is None:
        return None

    return (
        (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == user_message.conversation_id,
                    Message.role == "assistant",
                    Message.seq > user_message.seq,
                )
                .options(selectinload(Message.citations), selectinload(Message.feedback))
                .order_by(Message.seq)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


def build_ai_request(
    body: SendMessageRequest,
    *,
    scope_used: ScopeUsed,
    documents: list[Any],
    conversation_id: str,
    trace_id: str,
    user_id: str,
    history: list[tuple[str, str]],
    scenario: str | None,
) -> AiRequest:
    return AiRequest(
        text=body.text,
        mode=body.mode,
        scope_used=scope_used,
        documents=documents,
        conversation_id=conversation_id,
        trace_id=trace_id,
        user_id=user_id,
        history=history,
        draft_params=body.draft_params,
        scenario=scenario,
    )


async def recent_history(
    db: AsyncSession, conversation_id: str, limit: int = 8
) -> list[tuple[str, str]]:
    """The last N turns, oldest first. Unused by the fixture engine, and
    deliberately passed anyway: the M4 query rewriter needs it, and discovering
    the plumbing is missing at that point would mean touching every route."""
    rows = list(
        (
            await db.execute(
                select(Message.role, Message.text)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.seq.desc())
                .limit(limit)
            )
        ).all()
    )
    return [(role, text) for role, text in reversed(rows)]


def new_trace_id() -> str:
    return prefixed_id("tr")


def empty_scope(scope: Scope) -> ScopeUsed:
    return ScopeUsed(kind=scope.kind, document_ids=[], document_count=0, page_count=0, excluded=[])
