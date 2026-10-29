"""NFR-14: the AI layer is reachable only via `answer()`, `summarize()` and
`draft()`. The backend holds no retrieval or prompt logic.

BRD §7 states the consequence: when this later becomes a multi-agent system, the
change happens behind this interface and touches neither the frontend nor the
API contract. The interface is therefore the deliverable, and the M3 fixture
engine is just its first implementation.

Each method is an async iterator of `StreamEvent`, because streaming is in the
contract (FR-10) and a method returning a finished envelope would make
token-by-token delivery impossible to add later without changing every caller.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.schemas.common import Mode, ScopeUsed
from app.schemas.envelope import DraftParams
from app.schemas.events import StreamEvent


@dataclass
class ScopedDocument:
    """A document the engine is permitted to retrieve from, with everything it
    needs so the engine never touches the database."""

    id: str
    name: str
    type: str
    pages: int
    status: str


@dataclass
class TurnTrace:
    """NFR-11's record, filled in by the engine as the turn runs.

    An out-parameter rather than a return value, for the same reason
    `AskOutcome` is one: the ports are async generators, and an async generator
    cannot return anything. The route hands this object to the engine inside
    `AiRequest` and reads it after the stream finishes — including when the
    client disconnected mid-stream, which is exactly when a trace is worth
    having.

    Before this existed, `turns.py` reconstructed the trace from the event
    stream: `queries` came from scraping stage *detail strings* and
    `candidates` from the excerpt events. That worked against the fixture
    engine because the fixture engine wrote those strings for it. Against real
    orchestration it is wrong twice over — the query that actually ran is the
    *rewritten* one, which no event carries, and the candidate set is the 25
    retrieved, not the 10 that survived the floor and became excerpts. A trace
    whose candidate list silently omits everything that was rejected cannot
    diagnose the retrieval failures it exists to diagnose.
    """

    #: The queries actually sent to retrieval, in order. The rewritten one, not
    #: the one the attorney typed — that is `AiRequest.text`.
    queries: list[str] = field(default_factory=list)
    #: Every candidate retrieval considered, with its score and whether it
    #: survived. `kept=False` rows are the diagnostically useful ones.
    candidates: list[dict[str, Any]] = field(default_factory=list)
    #: Per-stage wall clock, plus `ttft_ms`. Merged into `timings_json`.
    timings_ms: dict[str, float] = field(default_factory=dict)
    #: One row per model call: which model, which prompt, tokens, cost. The
    #: `usage` event carries the turn's total; this says where it went, which is
    #: what NFR-8 needs when a turn spends on three models at three prices.
    calls: list[dict[str, Any]] = field(default_factory=list)
    #: Everything else worth recording and nothing worth a column: the routing
    #: decision and its source, the rewrite and why, what the citation gate
    #: stripped or refused.
    detail: dict[str, Any] = field(default_factory=dict)

    def record_call(
        self,
        stage: str,
        *,
        model: str | None,
        prompt_version: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        usd: float = 0.0,
        latency_ms: float = 0.0,
    ) -> None:
        self.calls.append(
            {
                "stage": stage,
                "model": model,
                "prompt": prompt_version,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_write_tokens": cache_write_tokens,
                "usd": round(usd, 6),
                "latency_ms": round(latency_ms, 1),
            }
        )

    def record_candidate(
        self,
        *,
        span_id: str,
        document_id: str,
        page: int,
        score: float,
        kept: bool,
        stage: str = "rerank",
    ) -> None:
        self.candidates.append(
            {
                "span_id": span_id,
                "document_id": document_id,
                "page": page,
                "score": round(score, 4),
                "kept": kept,
                "stage": stage,
            }
        )


@dataclass
class AiRequest:
    """What the orchestration layer will build at M4, and what the route builds
    today. Deliberately free of ORM objects and of `Request`: the engine must be
    callable from a test with a literal."""

    text: str
    mode: Mode
    scope_used: ScopeUsed
    documents: list[ScopedDocument]
    conversation_id: str
    trace_id: str
    user_id: str
    #: Prior turns, oldest first, for the query rewriter at M4.
    history: list[tuple[str, str]] = field(default_factory=list)
    draft_params: DraftParams | None = None
    #: Dev-only override from `X-LegalLens-Scenario`; None in production.
    scenario: str | None = None
    #: Written by the engine, read by the route after the stream. See `TurnTrace`.
    trace: TurnTrace = field(default_factory=TurnTrace)

    def document(self, document_id: str) -> ScopedDocument | None:
        return next((d for d in self.documents if d.id == document_id), None)

    @property
    def ready_documents(self) -> list[ScopedDocument]:
        """FR-7: only `ready` documents enter retrieval."""
        return [d for d in self.documents if d.status == "ready"]


class AiUnavailable(RuntimeError):
    """The provider is down or rate-limited. NFR-6: retrieval and excerpts keep
    working, so the route turns this into an error event carrying
    `excerptsAvailable: true` rather than failing the request outright."""

    def __init__(self, message: str, *, retry_after_sec: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_sec = retry_after_sec


@runtime_checkable
class AiEngine(Protocol):
    """The whole AI layer, as three callables.

    M3  `FixtureEngine` — fixture lookup plus realistic timings.
    M4  `LangChainEngine` — router, query rewrite, hybrid retrieval, rerank,
        parent expansion, grounded generation, citation gate.
    """

    # Declared as read-only properties rather than as settable attributes, so
    # an implementation may compute them. `FixtureEngine` has four literals;
    # `OrchestratedEngine` derives them from configuration, because its index
    # version depends on the embedding model and its prompt version on the
    # bytes of a file on disk (NFR-12 is only honest if the recorded version
    # comes from what was actually sent). A settable-attribute protocol member
    # rejects a property, which is what made this distinction matter.
    @property
    def name(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    @property
    def index_version(self) -> str: ...

    def answer(self, request: AiRequest) -> AsyncIterator[StreamEvent]: ...

    def summarize(self, request: AiRequest) -> AsyncIterator[StreamEvent]: ...

    def draft(self, request: AiRequest) -> AsyncIterator[StreamEvent]: ...

    async def route_intent(self, request: AiRequest) -> str:
        """Returns one of ask | summary | draft | out_of_scope.

        An explicit mode chip overrides the router — the architecture says so,
        and frontend nuance #4 requires the decision to be visible either way.

        Async because routing is a model call in every implementation that is
        not a keyword table. A blocking HTTP request here would stall the event
        loop for every other in-flight turn, and routing is the *first* thing in
        a turn: the stall would land squarely on NFR-1's p95 time-to-first-token
        for every concurrent user, not just the one being routed.
        """
        ...
