"""Conversations and turns. Mirrors `Conversation`, `UserTurn`, `AssistantTurn`
in types.ts. The discriminator the frontend switches on is `role`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from app.schemas.base import CamelModel, StrictCamelModel
from app.schemas.common import ApiError as ApiErrorModel
from app.schemas.common import (
    Citation,
    Coverage,
    Excerpt,
    FeedbackReason,
    FeedbackVerdict,
    Intent,
    Mode,
    OutputKind,
    Refusal,
    Scope,
    ScopeUsed,
    Stage,
    TurnStatus,
    Usage,
    Versions,
)
from app.schemas.envelope import DraftParams, DraftPayload, SummaryPayload


class Feedback(CamelModel):
    verdict: FeedbackVerdict
    reasons: list[FeedbackReason] = Field(default_factory=list)
    note: str | None = None


class UserTurn(CamelModel):
    id: str
    role: Literal["user"] = "user"
    text: str
    mode: Mode
    scope: Scope
    draft_params: DraftParams | None = None
    created_at: str


class AssistantTurn(CamelModel):
    id: str
    role: Literal["assistant"] = "assistant"
    status: TurnStatus
    kind: OutputKind
    # The mode the user picked, kept so `auto → ask` stays visible.
    requested_mode: Mode
    intent: Intent | None = None
    text: str = ""
    citations: list[Citation] = Field(default_factory=list)
    excerpts: list[Excerpt] = Field(default_factory=list)
    scope_used: ScopeUsed | None = None
    stages: list[Stage] = Field(default_factory=list)
    summary: SummaryPayload | None = None
    draft: DraftPayload | None = None
    refusal: Refusal | None = None
    error: ApiErrorModel | None = None
    coverage: Coverage | None = None
    usage: Usage | None = None
    versions: Versions | None = None
    trace_id: str | None = None
    disclaimer: str | None = None
    feedback: Feedback | None = None
    created_at: str


Turn = Annotated[UserTurn | AssistantTurn, Field(discriminator="role")]


class ConversationSummary(CamelModel):
    id: str
    title: str
    last_message_at: str
    pinned: bool
    dominant_kind: OutputKind
    turn_count: int


class Conversation(ConversationSummary):
    turns: list[Turn] = Field(default_factory=list)


class ConversationCreate(StrictCamelModel):
    title: str = "New chat"


class ConversationPatch(StrictCamelModel):
    """⊕ `pinned` is not in the architecture's endpoint list but client.ts
    calls `setPinned`, so it lands here rather than as a second route."""

    title: str | None = None
    pinned: bool | None = None


class ConversationPage(CamelModel):
    items: list[ConversationSummary] = Field(default_factory=list)
    next_cursor: str | None = None


class TurnPage(CamelModel):
    items: list[Turn] = Field(default_factory=list)
    next_cursor: str | None = None


class SendMessageRequest(StrictCamelModel):
    """Body of POST /api/conversations/{id}/messages. `conversation_id` is in
    the path; types.ts carries it in the object too, so it is accepted and
    ignored rather than rejected by `extra='forbid'`.

    `text` is required but may be **empty**, and the distinction is load-bearing.
    Summary is template-driven and ignores the question entirely
    (`SummaryWorkflow.run` takes `question: str = ""` and never reads it), so
    focus instructions are optional — which is what the composer's "(optional)"
    placeholder already promises the attorney. A `min_length=1` here made every
    summary sent with an empty box a 422 raised before the route body ran.

    No default, though: a body that omits `text` altogether is a client that
    forgot a field rather than an attorney who typed nothing, and it stays a
    422 naming the field.

    An empty question *is* meaningless in every other mode, so it is still
    rejected — in `turns._stream_turn`, beside the FR-19 draft guard, because
    only the route knows the resolved intent: `POST /api/summarize` carries
    whatever `mode` the caller sent and the route is what makes it a summary.
    """

    conversation_id: str | None = None
    text: str = Field(max_length=8000)
    mode: Mode = "auto"
    scope: Scope = Field(default_factory=Scope)
    draft_params: DraftParams | None = None


class ModeRequest(SendMessageRequest):
    """Body of the explicit-mode endpoints. `conversation_id` optional: when
    absent the endpoint creates a conversation, so /api/ask works standalone."""
