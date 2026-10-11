"""The router's vocabulary: what it is asked, and what it answers.

The four labels are not this module's invention — `Intent` in
`app/schemas/common.py` and `types.ts` already name them, and the turn route
already dispatches on them. Re-deriving the taxonomy here would let the two
drift, so `test_intent_router.py` asserts they are identical.

`RouterInput` exists rather than reusing `AiRequest` so the router is callable
from a test with a literal and carries no pydantic schema into its core. The
adapter from `AiRequest` is one classmethod, which is the whole integration
surface at M4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, get_args

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:  # pragma: no cover — import-free at runtime, see module docstring
    from app.ai.ports import AiRequest

Intent = Literal["ask", "summary", "draft", "out_of_scope"]
Mode = Literal["auto", "ask", "summary", "draft"]

#: The labels the model may return, in the order the prompt presents them.
INTENTS: tuple[Intent, ...] = get_args(Intent)

#: Where a decision came from. Only `model` spent a token.
DecisionSource = Literal["explicit_mode", "model", "heuristic_fallback", "default_fallback"]

#: The label used when everything else fails.
#:
#: `ask` and not `summary` or `draft`: the ask workflow is read-only and can
#: refuse in a well-formed way (FR-14), whereas a wrong `draft` produces an
#: unreviewed draft document — the failure mode FR-23 exists to contain. A
#: router outage must not be able to manufacture work-product.
SAFE_DEFAULT: Intent = "ask"


# The docstring of the model below is sent to Claude as the schema's
# `description`, so it is written for the model and kept to one line. The notes
# that belong to a maintainer live here instead:
#
# *Field order is load-bearing.* Claude's structured output follows the schema's
# property order and `properties` follows declaration order, so `rationale` is
# generated *before* `intent`. Reversing them turns the rationale into a
# post-hoc justification of a label already chosen — less accurate, and less
# useful in a trace.
#
# *`confidence` clamps instead of raising.* The JSON-schema conversion moves
# numeric bounds into the description rather than the schema, so the model is
# not hard-constrained to 0.0-1.0. A returned 1.2 is a well-formed label with a
# sloppy number attached, and failing the whole parse over it would demote a
# usable decision to a keyword fallback. The label is the product; the float is
# advice about the label.
class IntentClassification(BaseModel):
    """One routing decision for the attorney's request."""

    rationale: str = Field(
        description="One sentence, under 30 words, naming the decisive signal.",
    )
    intent: Intent = Field(
        description="Exactly one of ask, summary, draft, out_of_scope.",
    )
    confidence: float = Field(
        description="How much a careful attorney would agree with this label, from 0.0 to 1.0.",
    )

    @field_validator("confidence", mode="after")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return min(1.0, max(0.0, value))


@dataclass(frozen=True)
class ScopeFacts:
    """Document context, reduced to what routing can justify seeing.

    No names and no content. DR-5 treats every upload as privileged, and a
    decision between "answer this" and "summarise this" does not need the
    matter name to be correct. `test_intent_router.py` asserts no document name
    reaches the prompt.
    """

    document_count: int = 0
    document_types: tuple[str, ...] = ()
    single_document_in_focus: bool = False

    def as_prompt_line(self) -> str:
        if self.document_count == 0:
            return "No documents are in scope."
        types = ", ".join(sorted(set(self.document_types))) or "unknown"
        focus = (
            "exactly one document is in focus"
            if self.single_document_in_focus
            else "no single document is in focus"
        )
        plural = "" if self.document_count == 1 else "s"
        return f"{self.document_count} document{plural} in scope (types: {types}); {focus}."


@dataclass(frozen=True)
class RouterInput:
    """One routing question."""

    text: str
    mode: Mode = "auto"
    scope: ScopeFacts = field(default_factory=ScopeFacts)
    #: Prior turns as (role, text), oldest first. Truncated by the prompt builder.
    history: tuple[tuple[str, str], ...] = ()
    trace_id: str | None = None

    @classmethod
    def from_ai_request(cls, request: AiRequest) -> RouterInput:
        """The M4 integration point: `AiRequest` in, `RouterInput` out.

        Reads `ready_documents` rather than `documents` because FR-7 keeps
        anything not `ready` out of retrieval, and a document the answer cannot
        draw on should not sway how the question is routed.
        """
        ready = request.ready_documents
        return cls(
            text=request.text,
            mode=request.mode,
            scope=ScopeFacts(
                document_count=len(ready),
                document_types=tuple(d.type for d in ready),
                single_document_in_focus=request.scope_used.kind == "document",
            ),
            history=tuple(request.history),
            trace_id=request.trace_id,
        )


@dataclass(frozen=True)
class Usage:
    """Per-call cost and latency. NFR-8 attributes spend per request; NFR-11
    wants it per stage, and routing is a stage."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    latency_ms: float = 0.0

    @property
    def usd(self) -> float:
        """Claude Haiku 4.5: $1.00 per MTok in, $5.00 per MTok out. Cache reads
        bill at ~0.1x input and cache writes at ~1.25x."""
        return (
            self.input_tokens * 1.00
            + self.cache_read_input_tokens * 0.10
            + self.cache_creation_input_tokens * 1.25
            + self.output_tokens * 5.00
        ) / 1_000_000


@dataclass(frozen=True)
class IntentDecision:
    """The router's answer, with everything a trace needs to explain it.

    `intent` is always populated — the router has no failure return. A provider
    outage yields `source="heuristic_fallback"` or `"default_fallback"` with
    `error` set, because NFR-6 requires the turn to keep working when the LLM
    does not. Callers that would rather fail loudly check `error`.
    """

    intent: Intent
    confidence: float
    rationale: str
    source: DecisionSource
    usage: Usage = field(default_factory=Usage)
    #: Set when the model was not reached or not understood. `source` says what
    #: happened instead.
    error: str | None = None
    #: NFR-12: what produced this decision, so it is reproducible.
    model: str | None = None
    prompt_version: str | None = None
    trace_id: str | None = None

    @property
    def used_model(self) -> bool:
        return self.source == "model"

    @property
    def is_confident(self) -> bool:
        """Above this the product routes silently; below it the UI should offer
        a mode chip rather than guess (frontend nuance #4)."""
        return self.confidence >= 0.70
