"""Conversations and turn hydration.

The cursor is an opaque base64 of the sort key, not an offset. An offset cursor
breaks the moment a conversation is pinned or receives a message while the user
is scrolling, which on an infinite-scroll sidebar shows up as a duplicated or
skipped row.

Turn hydration reads `messages` + `message_citations` + `envelope_json` back
into the exact `UserTurn` / `AssistantTurn` shapes `types.ts` declares, so a
reloaded conversation renders identically to the one that just streamed.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Conversation, Message
from app.models.base import utcnow
from app.schemas.base import iso
from app.schemas.common import ApiError as ApiErrorModel
from app.schemas.common import (
    Citation,
    Coverage,
    Excerpt,
    Refusal,
    Scope,
    ScopeUsed,
    Stage,
    Usage,
    Versions,
)
from app.schemas.conversations import (
    AssistantTurn,
    ConversationPage,
    ConversationSummary,
    Turn,
    TurnPage,
    UserTurn,
)
from app.schemas.conversations import (
    Conversation as ConversationSchema,
)
from app.schemas.conversations import (
    Feedback as FeedbackSchema,
)
from app.schemas.envelope import DraftParams, DraftPayload, SummaryPayload

MAX_LIMIT = 50
DEFAULT_LIMIT = 25


# ------------------------------------------------------------------ cursors


def encode_cursor(pinned: bool, last_message_at: datetime, conversation_id: str) -> str:
    raw = json.dumps([1 if pinned else 0, iso(last_message_at), conversation_id])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[int, str, str] | None:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        pinned, timestamp, conversation_id = json.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, ValueError, TypeError):
        return None
    return int(pinned), str(timestamp), str(conversation_id)


def encode_seq_cursor(seq: int) -> str:
    return base64.urlsafe_b64encode(str(seq).encode()).decode().rstrip("=")


def decode_seq_cursor(cursor: str) -> int | None:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return int(base64.urlsafe_b64decode(padded))
    except (binascii.Error, ValueError):
        return None


# ------------------------------------------------------------------- reads


def to_summary(conversation: Conversation) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        last_message_at=iso(conversation.last_message_at) or "",
        pinned=conversation.pinned,
        dominant_kind=conversation.dominant_kind,
        turn_count=conversation.turn_count,
    )


async def list_conversations(
    db: AsyncSession,
    user_id: str,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
    query: str | None = None,
) -> ConversationPage:
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = select(Conversation).where(
        Conversation.user_id == user_id, Conversation.deleted_at.is_(None)
    )
    if query:
        stmt = stmt.where(Conversation.title.ilike(f"%{query.strip()}%"))

    # Pinned first, then most recent. The tie-break on id is what makes the
    # cursor total — two conversations can share a millisecond.
    pinned_sort = Conversation.pinned.desc()
    stmt = stmt.order_by(pinned_sort, Conversation.last_message_at.desc(), Conversation.id.desc())

    if cursor:
        decoded = decode_cursor(cursor)
        if decoded is not None:
            pinned_flag, timestamp, last_id = decoded
            cutoff = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))

            # Keyset predicate over (pinned DESC, last_message_at DESC, id DESC).
            # Written as two explicit cases rather than one comparison chain,
            # because `Conversation.pinned < False` is not a legal SQLAlchemy
            # expression — a Boolean column only supports equality operators.
            after_within_block = or_(
                Conversation.last_message_at < cutoff,
                (Conversation.last_message_at == cutoff) & (Conversation.id < last_id),
            )
            if pinned_flag:
                # Still inside the pinned block: the rest of it, then everything
                # unpinned.
                stmt = stmt.where(
                    or_(
                        Conversation.pinned.is_(False),
                        Conversation.pinned.is_(True) & after_within_block,
                    )
                )
            else:
                # Past the pinned block already — no pinned row can follow.
                stmt = stmt.where(Conversation.pinned.is_(False) & after_within_block)

    rows = list((await db.execute(stmt.limit(limit + 1))).scalars())
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = (
        encode_cursor(rows[-1].pinned, rows[-1].last_message_at, rows[-1].id)
        if has_more and rows
        else None
    )
    return ConversationPage(items=[to_summary(c) for c in rows], next_cursor=next_cursor)


async def get_conversation(
    db: AsyncSession, user_id: str, conversation_id: str
) -> Conversation | None:
    stmt = (
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.deleted_at.is_(None),
        )
        # Both relationships `to_turn` touches must be eager-loaded: a lazy
        # load under async SQLAlchemy raises MissingGreenlet rather than
        # quietly issuing a query.
        .options(
            selectinload(Conversation.messages).selectinload(Message.citations),
            selectinload(Conversation.messages).selectinload(Message.feedback),
        )
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def hydrate(db: AsyncSession, conversation: Conversation) -> ConversationSchema:
    summary = to_summary(conversation)
    return ConversationSchema(
        **summary.model_dump(),
        turns=[to_turn(m) for m in sorted(conversation.messages, key=lambda m: m.seq)],
    )


async def list_turns(
    db: AsyncSession,
    conversation_id: str,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> TurnPage:
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .options(selectinload(Message.citations), selectinload(Message.feedback))
        .order_by(Message.seq)
    )
    if cursor:
        after = decode_seq_cursor(cursor)
        if after is not None:
            stmt = stmt.where(Message.seq > after)

    rows = list((await db.execute(stmt.limit(limit + 1))).scalars())
    has_more = len(rows) > limit
    rows = rows[:limit]
    return TurnPage(
        items=[to_turn(m) for m in rows],
        next_cursor=encode_seq_cursor(rows[-1].seq) if has_more and rows else None,
    )


def to_turn(message: Message) -> Turn:
    if message.role == "user":
        return UserTurn(
            id=message.id,
            text=message.text,
            mode=message.mode or "auto",
            scope=Scope.model_validate(message.scope_json or {"kind": "workspace"}),
            draft_params=(
                DraftParams.model_validate(message.draft_params_json)
                if message.draft_params_json
                else None
            ),
            created_at=iso(message.created_at) or "",
        )

    envelope = message.envelope_json or {}
    return AssistantTurn(
        id=message.id,
        status=message.status or "complete",
        kind=message.kind or "answer",
        requested_mode=message.requested_mode or "auto",
        intent=message.intent,
        text=message.text,
        # Citations come from the normalised table rather than the envelope:
        # the table is what the FR-21 / NFR-15 precision query reads, so a
        # divergence between the two has to be impossible to render.
        citations=[
            Citation(
                n=c.n,
                span_id=c.span_id,
                document_id=c.document_id,
                document_name=c.document_name,
                page=c.page,
                section=c.section,
                label=c.label,
            )
            for c in sorted(message.citations, key=lambda c: c.n)
        ],
        excerpts=[Excerpt.model_validate(e) for e in envelope.get("excerpts", [])],
        scope_used=(
            ScopeUsed.model_validate(envelope["scope_used"]) if envelope.get("scope_used") else None
        ),
        stages=[Stage.model_validate(s) for s in envelope.get("stages", [])],
        summary=(
            SummaryPayload.model_validate(envelope["summary"]) if envelope.get("summary") else None
        ),
        draft=DraftPayload.model_validate(envelope["draft"]) if envelope.get("draft") else None,
        refusal=Refusal.model_validate(envelope["refusal"]) if envelope.get("refusal") else None,
        error=(ApiErrorModel.model_validate(envelope["error"]) if envelope.get("error") else None),
        coverage=(
            Coverage.model_validate(envelope["coverage"]) if envelope.get("coverage") else None
        ),
        usage=Usage.model_validate(envelope["usage"]) if envelope.get("usage") else None,
        versions=(
            Versions.model_validate(envelope["versions"]) if envelope.get("versions") else None
        ),
        trace_id=message.trace_id,
        disclaimer=envelope.get("disclaimer"),
        feedback=(
            FeedbackSchema(
                verdict=message.feedback.verdict,
                reasons=message.feedback.reasons_json,
                note=message.feedback.note,
            )
            if message.feedback
            else None
        ),
        created_at=iso(message.created_at) or "",
    )


# ------------------------------------------------------------------ writes


async def create(db: AsyncSession, user_id: str, title: str = "New chat") -> Conversation:
    """The NCC endpoint's work."""
    conversation = Conversation(
        user_id=user_id,
        title=(title or "New chat").strip()[:300] or "New chat",
        last_message_at=utcnow(),
    )
    db.add(conversation)
    await db.flush()
    return conversation


async def next_seq(db: AsyncSession, conversation_id: str) -> int:
    result = await db.execute(
        select(func.coalesce(func.max(Message.seq), -1)).where(
            Message.conversation_id == conversation_id
        )
    )
    return int(result.scalar_one()) + 1


async def touch(db: AsyncSession, conversation: Conversation, *, kind: str | None = None) -> None:
    """Maintained on write rather than computed per read — the sidebar reads
    these on every page and would otherwise aggregate messages each time."""
    conversation.last_message_at = utcnow()
    conversation.turn_count = int(
        (
            await db.execute(
                select(func.count(Message.id)).where(Message.conversation_id == conversation.id)
            )
        ).scalar_one()
    )
    if kind:
        conversation.dominant_kind = kind


async def autotitle(db: AsyncSession, conversation: Conversation, text: str) -> None:
    """First user message names an untitled thread. The frontend shows the
    title in the sidebar immediately, so "New chat" × 10 is a real usability
    problem rather than a cosmetic one."""
    if conversation.title not in ("New chat", "", None):
        return
    cleaned = " ".join(text.split())
    conversation.title = (cleaned[:57] + "…") if len(cleaned) > 58 else cleaned or "New chat"
