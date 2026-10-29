"""The SSE event union. Mirrors `StreamEvent` in types.ts — thirteen variants,
discriminated on `type`, which is carried *inside* the JSON so a plain-JSON
consumer and an EventSource-style consumer both work.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from app.schemas.base import CamelModel
from app.schemas.common import (
    ApiError,
    Citation,
    Coverage,
    DocType,
    DraftType,
    Excerpt,
    Intent,
    Mode,
    Refusal,
    ScopeUsed,
    Stage,
    Usage,
    Versions,
)
from app.schemas.envelope import DraftParams, DraftSection, SummaryField


class StageEvent(CamelModel):
    type: Literal["stage"] = "stage"
    stage: Stage


class IntentEvent(CamelModel):
    type: Literal["intent"] = "intent"
    intent: Intent
    mode: Mode


class ScopeEvent(CamelModel):
    type: Literal["scope"] = "scope"
    scope_used: ScopeUsed


class TokenEvent(CamelModel):
    type: Literal["token"] = "token"
    text: str


class CitationEvent(CamelModel):
    type: Literal["citation"] = "citation"
    citation: Citation


class ExcerptEvent(CamelModel):
    type: Literal["excerpt"] = "excerpt"
    excerpt: Excerpt


class SummaryHeader(CamelModel):
    """`Omit<SummaryPayload, 'fields'>` — which document and template the
    fields about to arrive belong to."""

    document_id: str
    document_name: str
    document_type: DocType
    template_name: str
    coverage: Coverage | None = None


class SummaryStartEvent(CamelModel):
    """Header for a summary, sent before its fields so the card can render.

    Without this the client would have no source for `templateName` once it
    stops carrying BRD §9's template names itself.
    """

    type: Literal["summary_start"] = "summary_start"
    summary: SummaryHeader
    field_count: int


class SummaryFieldEvent(CamelModel):
    type: Literal["summary_field"] = "summary_field"
    field: SummaryField
    index: int
    total: int


class DraftHeader(CamelModel):
    """`Omit<DraftPayload, 'sections'>` — the shell a draft fills in."""

    draft_type: DraftType
    title: str
    params: DraftParams


class DraftStartEvent(CamelModel):
    """Shell for a draft, sent before its sections arrive."""

    type: Literal["draft_start"] = "draft_start"
    draft: DraftHeader


class DraftSectionEvent(CamelModel):
    type: Literal["draft_section"] = "draft_section"
    section: DraftSection


class RefusalEvent(CamelModel):
    type: Literal["refusal"] = "refusal"
    refusal: Refusal


class UsageEvent(CamelModel):
    type: Literal["usage"] = "usage"
    usage: Usage
    versions: Versions
    trace_id: str


class CoverageEvent(CamelModel):
    type: Literal["coverage"] = "coverage"
    coverage: Coverage


class DoneEvent(CamelModel):
    type: Literal["done"] = "done"
    disclaimer: str


class ErrorEvent(CamelModel):
    type: Literal["error"] = "error"
    error: ApiError


StreamEvent = Annotated[
    StageEvent
    | IntentEvent
    | ScopeEvent
    | TokenEvent
    | CitationEvent
    | ExcerptEvent
    | SummaryStartEvent
    | SummaryFieldEvent
    | DraftStartEvent
    | DraftSectionEvent
    | RefusalEvent
    | UsageEvent
    | CoverageEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="type"),
]

StreamEventAdapter: TypeAdapter[StreamEvent] = TypeAdapter(StreamEvent)

#: Every variant name, asserted against types.ts by test_openapi_contract.
EVENT_TYPES: tuple[str, ...] = (
    "stage",
    "intent",
    "scope",
    "token",
    "citation",
    "excerpt",
    "summary_start",
    "summary_field",
    "draft_start",
    "draft_section",
    "refusal",
    "usage",
    "coverage",
    "done",
    "error",
)
