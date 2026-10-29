"""Enums and small shared shapes. Literal types mirror the TS unions exactly,
so a drift between the two files fails a test rather than a user's request."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.base import CamelModel, StrictCamelModel

Mode = Literal["auto", "ask", "summary", "draft"]
Intent = Literal["ask", "summary", "draft", "out_of_scope"]
OutputKind = Literal["answer", "summary", "draft"]

DocType = Literal["contract", "opinion", "pleading", "other"]
DocStatus = Literal["queued", "parsing", "ocr", "indexing", "ready", "failed"]
TypeSource = Literal["detected", "overridden"]

ScopeKind = Literal["document", "subset", "workspace"]
TurnStatus = Literal["streaming", "complete", "refused", "error", "incomplete"]
DraftType = Literal["memo", "case_brief"]

RefusalKind = Literal["no_support", "out_of_scope", "below_threshold"]
SuggestionKind = Literal["widen_scope", "upload", "rephrase", "switch_mode"]
ApiErrorKind = Literal["llm_unavailable", "rate_limited", "parse_failed", "timeout", "network"]

FeedbackVerdict = Literal["up", "down"]
FeedbackReason = Literal[
    "wrong_citation",
    "missed_content",
    "fabricated",
    "wrong_refusal",
    "wrong_structure",
    "other",
]

StageKey = Literal[
    "routing",
    "retrieving",
    "reranking",
    "expanding",
    "assembling",
    "writing",
    "verifying",
    "classifying",
    "field_retrieval",
    "mapping",
    "composing",
]

IngestionStepStatus = Literal["done", "active", "pending", "failed"]


class Scope(StrictCamelModel):
    kind: ScopeKind = "workspace"
    document_ids: list[str] = Field(default_factory=list)


class ExcludedDocument(CamelModel):
    document_id: str
    name: str
    reason: str


class ScopeUsed(CamelModel):
    """FR-9: echoed on every response. FR-7: `excluded` names what fell out."""

    kind: ScopeKind
    document_ids: list[str]
    document_count: int
    page_count: int
    excluded: list[ExcludedDocument] = Field(default_factory=list)


class Span(CamelModel):
    """FR-11. Offsets are page-local — see BACKEND-TODO §8.2."""

    id: str
    document_id: str
    page: int
    char_start: int
    char_end: int
    section: str | None = None


class Citation(CamelModel):
    n: int
    span_id: str
    document_id: str
    document_name: str
    page: int
    section: str | None = None
    label: str


class Excerpt(CamelModel):
    """FR-12: shown beside the answer with no further user action."""

    span_id: str
    document_id: str
    document_name: str
    page: int
    section: str | None = None
    before: str
    text: str
    after: str
    relevance: float


class Stage(CamelModel):
    key: StageKey
    label: str
    detail: str | None = None
    status: Literal["active", "done"]
    elapsed_ms: float


class Usage(CamelModel):
    input_tokens: int
    output_tokens: int
    usd: float
    latency_ms: float
    time_to_first_token_ms: float


class Versions(CamelModel):
    """NFR-12: what produced this answer, so it is reproducible."""

    model: str
    prompt: str
    index: str


class RefusalSuggestion(CamelModel):
    kind: SuggestionKind
    label: str
    detail: str | None = None


class Refusal(CamelModel):
    """FR-14 / TS-3. A first-class outcome, distinct in type from an error."""

    kind: RefusalKind
    message: str
    looked_for: list[str] = Field(default_factory=list)
    best_relevance: float | None = None
    threshold: float | None = None
    suggestions: list[RefusalSuggestion] = Field(default_factory=list)


class ApiError(CamelModel):
    """FR-28: every variant names a next action. Used as an HTTP error body and
    as the `{type:'error'}` stream event, so one frontend parser handles both."""

    kind: ApiErrorKind
    message: str
    action: str
    retry_after_sec: int | None = None
    document_id: str | None = None
    # NFR-6: retrieval survives an LLM outage, so excerpts may still exist.
    excerpts_available: bool | None = None
    request_id: str | None = None


class Coverage(CamelModel):
    """FR-17: what a composed long-document summary actually covered."""

    pages_covered: int
    total_pages: int
    passes: int
    note: str
