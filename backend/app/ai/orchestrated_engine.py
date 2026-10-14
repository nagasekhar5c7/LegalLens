"""The M4 implementation of `AiEngine`. The backend's one door into orchestration.

This is the file `app/ai/ports.py` has been describing since M3: the thing that
replaces `FixtureEngine` without a route, a schema or a frontend file changing.
It owns three jobs and deliberately no others.

**It adapts.** `AiRequest` (resolved scope, ORM-free documents, history) into
the arguments each workflow takes, and each workflow's outcome object into
`TurnTrace` for the route to persist. The workflows know nothing about
`AiRequest`, `TurnTrace` or the port, which is what keeps them testable with
literals and keeps `app/ai/orchestration` free of the backend's shapes.

**It emits the turn's envelope events.** `stage(routing)`, `intent` and `scope`
belong to the turn rather than to any one workflow — all three workflows need
them, in the same order, before their own first stage. The workflows emit from
their own first stage onward. The split is why `AskWorkflow` can be driven
directly by `scripts/try_ask.py` without inventing a scope it does not have.

**It decides the three things that precede a workflow.** Whether anything is in
scope at all (refuse before spending a token), whether the router said this is
not something Legal Lens does, and which document a summary is of. None of
those is a retrieval question, so none of them belongs inside a workflow.

Everything expensive is lazy. Constructing this engine opens no HTTP client,
loads no model and takes no Qdrant lock — `get_engine()` runs at import time on
a process that may never serve a turn, and bge-m3 plus the reranker are ~4.5 GB
between them. The first turn pays for the first load; `_retriever()` and the
workflow properties hold them afterwards.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from typing import Any

from app.ai.disclaimers import disclaimer_for
from app.ai.ports import AiRequest, AiUnavailable, ScopedDocument
from app.ai.scenarios import SCENARIO_ERRORS
from app.schemas.common import ApiError, Refusal, RefusalSuggestion, Stage
from app.schemas.envelope import DraftParams
from app.schemas.events import (
    DoneEvent,
    ErrorEvent,
    IntentEvent,
    RefusalEvent,
    ScopeEvent,
    StageEvent,
    StreamEvent,
)

log = logging.getLogger("legallens.ai.engine")

#: Stages after which a forced scenario error is honest.
#:
#: `X-LegalLens-Scenario` has to keep working against this engine or the
#: frontend's Demo menu stops reaching FR-28 and NFR-6 — but the error must land
#: where a real provider failure lands, which is *after* retrieval. Injected
#: before it, `excerptsAvailable: true` would be a lie and the calm "read the
#: passages instead" path would be untestable. Keyed per workflow because each
#: one's last pre-generation stage differs.
_PRE_GENERATION_STAGE: frozenset[str] = frozenset({"assembling", "mapping", "composing"})


class OrchestratedEngine:
    """Router + ask / summary / draft workflows, behind the three-callable port."""

    name = "orchestrated/m4"

    def __init__(self, settings: Any | None = None) -> None:
        from app.ai.orchestration.settings import get_router_settings

        self.settings = settings or get_router_settings()
        # Reentrant: the workflow builders acquire this and then call
        # `_retriever()` / `_get_rewriter()`, which acquire it again. A plain
        # Lock self-deadlocks there, on the event-loop thread, on the first turn.
        self._lock = threading.RLock()
        self._retriever_cache: Any | None = None
        self._router: Any | None = None
        self._rewriter: Any | None = None
        self._ask: Any | None = None
        self._summary: Any | None = None
        self._draft: Any | None = None

    # ------------------------------------------------------------- versions

    @property
    def prompt_version(self) -> str:
        """The ask prompt's stamp. NFR-12 records the prompt that produced the
        output, and `UsageEvent.versions` carries the per-workflow one; this is
        the engine-level default the port requires, and ask is the default
        workflow."""
        from app.ai.orchestration.prompt import load_named_prompt

        return load_named_prompt("ask_generation").version

    @property
    def model_version(self) -> str:
        return self.settings.ask_model

    @property
    def index_version(self) -> str:
        """Derived from configuration, not from the live store.

        Reading `retriever.store.name` here would open Qdrant — and this
        property is read by `/readyz`, which must answer without taking an
        exclusive directory lock the ingestion worker may hold.
        """
        model = self.settings.embed_model.rsplit("/", 1)[-1]
        where = self.settings.qdrant_url or self.settings.qdrant_path
        return f"{model}+qdrant:{self.settings.qdrant_collection}@{where}"

    # -------------------------------------------------------------- wiring

    def _retriever(self) -> Any:
        """The shared retrieval stack. One per process — see `retrieval.factory`."""
        if self._retriever_cache is None:
            with self._lock:
                if self._retriever_cache is None:
                    from app.ai.orchestration import build_retriever

                    self._retriever_cache = build_retriever(self.settings)
        return self._retriever_cache

    def _get_router(self) -> Any:
        if self._router is None:
            with self._lock:
                if self._router is None:
                    from app.ai.orchestration.router import IntentRouter

                    self._router = IntentRouter(self.settings)
        return self._router

    def _get_rewriter(self) -> Any:
        if self._rewriter is None:
            with self._lock:
                if self._rewriter is None:
                    from app.ai.orchestration.rewriter import QueryRewriter

                    self._rewriter = QueryRewriter(self.settings)
        return self._rewriter

    def _ask_workflow(self) -> Any:
        if self._ask is None:
            with self._lock:
                if self._ask is None:
                    from app.ai.orchestration.workflows.ask import AskWorkflow

                    self._ask = AskWorkflow(
                        retriever=self._retriever(),
                        rewriter=self._get_rewriter(),
                        settings=self.settings,
                    )
        return self._ask

    def _summary_workflow(self) -> Any:
        if self._summary is None:
            with self._lock:
                if self._summary is None:
                    from app.ai.orchestration.workflows.summary import SummaryWorkflow

                    self._summary = SummaryWorkflow(
                        retriever=self._retriever(), settings=self.settings
                    )
        return self._summary

    def _draft_workflow(self) -> Any:
        if self._draft is None:
            with self._lock:
                if self._draft is None:
                    from app.ai.orchestration.workflows.draft import DraftWorkflow

                    self._draft = DraftWorkflow(retriever=self._retriever(), settings=self.settings)
        return self._draft

    # ------------------------------------------------------------- routing

    async def route_intent(self, request: AiRequest) -> str:
        """One Haiku call, or none when the attorney pressed a mode chip.

        The decision is stashed on `request.trace` rather than only returned,
        for two reasons. The route calls this and *then* calls `answer()` on the
        same request, and `answer()` needs the rationale and confidence to emit
        the `stage(routing)` detail and to know whether the router said
        `out_of_scope` — re-classifying inside `answer()` would pay for the same
        call twice. And NFR-11 wants the routing decision on the trace whatever
        the turn then did with it.
        """
        from app.ai.orchestration.intent import RouterInput

        decision = await self._get_router().aclassify(RouterInput.from_ai_request(request))
        request.trace.detail["routing"] = {
            "intent": decision.intent,
            "confidence": round(decision.confidence, 3),
            "rationale": decision.rationale,
            "source": decision.source,
            "model": decision.model,
            "prompt": decision.prompt_version,
            "error": decision.error,
        }
        if decision.used_model:
            request.trace.record_call(
                "routing",
                model=decision.model,
                prompt_version=decision.prompt_version,
                input_tokens=decision.usage.input_tokens,
                output_tokens=decision.usage.output_tokens,
                cache_read_tokens=decision.usage.cache_read_input_tokens,
                cache_write_tokens=decision.usage.cache_creation_input_tokens,
                usd=decision.usage.usd,
                latency_ms=decision.usage.latency_ms,
            )
        if decision.error:
            log.warning("routing degraded to %s: %s", decision.source, decision.error)
        return decision.intent

    # --------------------------------------------------------- the three ports

    async def answer(self, request: AiRequest) -> AsyncIterator[StreamEvent]:
        """Grounded question answering, or a refusal.

        `out_of_scope` arrives here rather than as a fourth port method, because
        "this is not something Legal Lens does" is an outcome of asking and
        NFR-14 keeps the AI layer to three callables.

        One deliberate difference from `FixtureEngine`: the out-of-scope refusal
        fires only when the **router** said so, never from a keyword scan of the
        text. An attorney who pressed "Ask" gets the ask workflow, and a
        question the corpus cannot support comes back as retrieval's own
        `below_threshold` refusal — which names what was searched and what it
        scored, where a keyword match on "translate" cannot.
        """
        routing = request.trace.detail.get("routing") or {}
        intent = "out_of_scope" if routing.get("intent") == "out_of_scope" else "ask"

        async for event in self._open(request, intent):
            yield event
        if self._refused_early(request):
            async for event in self._refuse_empty_scope(request, "answer"):
                yield event
            return

        if intent == "out_of_scope":
            async for event in self._out_of_scope(request):
                yield event
            return

        from app.ai.orchestration.workflows.ask import AskOutcome

        outcome = AskOutcome()
        workflow = self._ask_workflow()
        stream = workflow.run(
            request.text,
            document_ids=list(request.scope_used.document_ids),
            history=tuple(request.history),
            trace_id=request.trace_id,
            scope_label=_describe_scope(request),
            outcome=outcome,
        )
        try:
            async for event in self._with_scenario(request, stream):
                yield event
        finally:
            # In a `finally` so the trace is written even when the client
            # disconnects mid-answer and this generator is closed from outside.
            # That is precisely the turn whose trace matters most, because the
            # record is all anyone will ever see of it (NFR-7).
            _trace_ask(request, outcome, workflow)

    async def summarize(self, request: AiRequest) -> AsyncIterator[StreamEvent]:
        """Schema-driven summary of one document."""
        async for event in self._open(request, "summary"):
            yield event
        if self._refused_early(request):
            async for event in self._refuse_empty_scope(request, "summary"):
                yield event
            return

        document = _summary_target(request)
        if document is None:
            yield RefusalEvent(
                refusal=Refusal(
                    kind="out_of_scope",
                    message=(
                        "No document in scope is ready to summarise. Summary runs against "
                        "one document at a time."
                    ),
                    suggestions=[
                        RefusalSuggestion(kind="upload", label="Upload a document", detail=None)
                    ],
                )
            )
            yield DoneEvent(disclaimer=disclaimer_for("summary"))
            return

        from app.ai.orchestration.workflows.summary import SummaryOutcome

        outcome = SummaryOutcome()
        workflow = self._summary_workflow()
        stream = workflow.run(
            document_id=document.id,
            document_name=document.name,
            document_type=document.type,
            document_pages=document.pages,
            question=request.text,
            trace_id=request.trace_id,
            outcome=outcome,
        )
        try:
            async for event in self._with_scenario(request, stream):
                yield event
        finally:
            _trace_summary(request, outcome, workflow)

    async def draft(self, request: AiRequest) -> AsyncIterator[StreamEvent]:
        """Grounded drafting from the attorney's selected sources."""
        async for event in self._open(request, "draft"):
            yield event
        if self._refused_early(request):
            async for event in self._refuse_empty_scope(request, "draft"):
                yield event
            return

        params = _draft_params(request)
        from app.ai.orchestration.workflows.draft import DraftOutcome

        outcome = DraftOutcome()
        workflow = self._draft_workflow()
        stream = workflow.run(
            params=params,
            question=request.text,
            document_names={d.id: d.name for d in request.documents},
            trace_id=request.trace_id,
            outcome=outcome,
        )
        try:
            async for event in self._with_scenario(request, stream):
                yield event
        finally:
            _trace_draft(request, outcome, workflow)

    # -------------------------------------------------- the turn's envelope

    async def _open(self, request: AiRequest, intent: str) -> AsyncIterator[StreamEvent]:
        """`stage(routing)` → `intent` → `scope`, the order the M3 contract set.

        Emitted here rather than inside a workflow because all three need it
        identically, and because a workflow driven from a script has no scope to
        report. `TurnAccumulator` reads `intent` to decide the turn's `kind` and
        `scope` to persist `scopeUsed`, so a workflow that skipped them would
        produce a turn the thread cannot re-render.
        """
        routing = request.trace.detail.get("routing") or {}
        if routing:
            source = routing.get("source", "model")
            confidence = routing.get("confidence")
            detail = f"{source} · {routing.get('intent')}"
            if source == "model" and confidence is not None:
                detail += f" · confidence {confidence:.2f}"
            if routing.get("error"):
                detail += " · degraded"
        else:
            # No routing happened: the attorney pressed a mode chip, and the
            # trace should say that rather than implying a classifier ran.
            detail = f"mode: {request.mode} · router not consulted"

        # The measured routing latency, not 0.0. The stage trace is rendered as
        # a cumulative timeline, and a first stage claiming to take no time makes
        # every later stage's elapsed figure read as if routing were free — which
        # on `auto` it is not: it is a model call on the NFR-1 critical path,
        # before retrieval has started.
        latency = next(
            (float(c["latency_ms"]) for c in request.trace.calls if c["stage"] == "routing"),
            0.0,
        )
        for status, elapsed in (("active", 0.0), ("done", latency)):
            yield StageEvent(
                stage=Stage(
                    key="routing",
                    label=_STAGE_LABEL["routing"],
                    detail=detail,
                    status=status,
                    elapsed_ms=elapsed,
                )
            )
        yield IntentEvent(intent=intent, mode=request.mode)
        yield ScopeEvent(scope_used=request.scope_used)

    @staticmethod
    def _refused_early(request: AiRequest) -> bool:
        """FR-7: nothing ready in scope means nothing to retrieve."""
        return request.scope_used.document_count == 0

    async def _refuse_empty_scope(
        self, request: AiRequest, kind: str
    ) -> AsyncIterator[StreamEvent]:
        """Refuse before a token is spent, naming what fell out and why.

        The `excluded` list is the part that earns its keep: "no documents in
        scope" and "the three you selected are all still indexing" are different
        problems, and only the second tells the attorney to wait rather than to
        upload (frontend nuance #7).
        """
        excluded = len(request.scope_used.excluded)
        if excluded:
            was = " was" if excluded == 1 else "s were"
            is_ = "it is" if excluded == 1 else "they are"
            message = (
                f"No document in scope is ready to search. {excluded} document{was} "
                f"excluded because {is_} still processing or failed to parse."
            )
        else:
            message = "No documents are in scope, so there is nothing to search."
        yield RefusalEvent(
            refusal=Refusal(
                kind="out_of_scope",
                message=message,
                looked_for=[],
                suggestions=[
                    RefusalSuggestion(
                        kind="widen_scope", label="Search the whole workspace", detail=None
                    ),
                    RefusalSuggestion(kind="upload", label="Upload a document", detail=None),
                ],
            )
        )
        yield DoneEvent(disclaimer=disclaimer_for(kind))

    async def _out_of_scope(self, request: AiRequest) -> AsyncIterator[StreamEvent]:
        """The router said this is not something Legal Lens does.

        No retrieval runs, because there is nothing to retrieve *for* — and the
        refusal says so explicitly rather than reporting an empty search, which
        would imply the corpus was the problem.
        """
        rationale = (request.trace.detail.get("routing") or {}).get("rationale") or ""
        for status in ("active", "done"):
            yield StageEvent(
                stage=Stage(
                    key="retrieving",
                    label=_STAGE_LABEL["retrieving"],
                    detail="out of scope — no retrieval run",
                    status=status,
                    elapsed_ms=0.0,
                )
            )
        yield RefusalEvent(
            refusal=Refusal(
                kind="out_of_scope",
                message=(
                    "Legal Lens reads and drafts from documents you have uploaded. This "
                    "request is not a question about those documents"
                    + (f" — {rationale[0].lower() + rationale[1:]}" if rationale else "")
                    + ". Nothing was searched, and nothing is being answered from the "
                    "model's own knowledge."
                ),
                looked_for=[],
                suggestions=[
                    RefusalSuggestion(
                        kind="rephrase",
                        label="Ask about a document in your workspace",
                        detail="Questions are answered only from the documents in scope.",
                    ),
                ],
            )
        )
        yield DoneEvent(disclaimer=disclaimer_for("answer"))

    async def _with_scenario(
        self, request: AiRequest, stream: AsyncIterator[StreamEvent]
    ) -> AsyncIterator[StreamEvent]:
        """Forward a workflow's events, honouring a forced error scenario.

        Dev only — `deps.scenario` returns None in production. The error is
        injected after the last pre-generation stage, which is where a real
        provider failure lands, so `excerptsAvailable` is true because the
        excerpts genuinely were emitted.
        """
        forced = SCENARIO_ERRORS.get(request.scenario or "")
        if not forced:
            async for event in stream:
                yield event
            return

        async for event in stream:
            yield event
            if (
                isinstance(event, StageEvent)
                and event.stage.status == "done"
                and event.stage.key in _PRE_GENERATION_STAGE
            ):
                log.info("scenario %r: injecting a forced error", request.scenario)
                yield ErrorEvent(error=ApiError.model_validate(forced))
                return


# ------------------------------------------------------------------- labels

#: Imported lazily-by-copy rather than from `workflows.stages`, which is where
#: it lives, to keep this module importable on a checkout without the `ai`
#: extra — `/readyz` constructs this engine to report why it is unavailable.
_STAGE_LABEL: dict[str, str] = {
    "routing": "Routing the question",
    "retrieving": "Hybrid retrieval",
}


# ---------------------------------------------------------------- selection


def _summary_target(request: AiRequest) -> ScopedDocument | None:
    """Which document a summary is of.

    Order: an explicit `draft_params.document_ids` selection, then the resolved
    scope, then the first ready document. Summary is per document (FR-15), and
    the scope resolver has already dropped anything not `ready`, so the first
    entry of `scope_used.document_ids` is both in scope and searchable.
    """
    params = request.draft_params
    if params is not None and params.document_ids:
        found = request.document(params.document_ids[0])
        if found is not None and found.status == "ready":
            return found
    for document_id in request.scope_used.document_ids:
        found = request.document(document_id)
        if found is not None:
            return found
    return next(iter(request.ready_documents), None)


def _draft_params(request: AiRequest) -> DraftParams:
    """The draft's parameters, defaulted from the turn when the client sent none.

    The route refuses a draft with no source documents before reaching the
    engine (FR-19), so the fallback below cannot produce a sourceless draft
    through `/api/conversations/{id}/messages` either: `scope_used.document_ids`
    is empty exactly when `_refused_early` has already returned.
    """
    params = request.draft_params
    if params is not None and params.document_ids:
        return params
    return DraftParams(
        draft_type=params.draft_type if params is not None else "memo",
        question_presented=(params.question_presented if params is not None else "")
        or request.text,
        audience=params.audience if params is not None else "supervising partner",
        position=params.position if params is not None else "",
        tone=params.tone if params is not None else "neutral predictive",
        document_ids=list(request.scope_used.document_ids),
    )


def _describe_scope(request: AiRequest) -> str:
    """The phrase a refusal uses to name what was actually searched (FR-14)."""
    scope = request.scope_used
    if scope.document_count == 0:
        return "the selected documents"
    if scope.kind == "document":
        document = request.document(scope.document_ids[0]) if scope.document_ids else None
        return f"“{document.name}”" if document else "this document"
    if scope.kind == "workspace":
        return f"your workspace ({scope.document_count} documents, {scope.page_count} pages)"
    return f"the {scope.document_count} selected documents"


# -------------------------------------------------------------------- traces


def _trace_ask(request: AiRequest, outcome: Any, workflow: Any) -> None:
    """`AskOutcome` → `TurnTrace`. NFR-11, from facts rather than from strings."""
    trace = request.trace
    rewrite = outcome.rewrite
    trace.queries = [rewrite.query] if rewrite is not None else [request.text]
    if rewrite is not None:
        trace.detail["rewrite"] = {
            "query": rewrite.query,
            "original": rewrite.original,
            "source": rewrite.source,
            "rewritten": rewrite.rewritten,
            "reasoning": rewrite.reasoning,
            "referents": list(rewrite.referents),
            "error": rewrite.error,
        }
        if rewrite.used_model:
            trace.record_call(
                "rewrite",
                model=rewrite.model,
                prompt_version=rewrite.prompt_version,
                input_tokens=rewrite.usage.input_tokens,
                output_tokens=rewrite.usage.output_tokens,
                usd=rewrite.usage.usd,
                latency_ms=rewrite.usage.latency_ms,
            )

    _record_candidates(request, outcome.retrieval)
    trace.timings_ms.update(outcome.timings_ms)
    trace.detail["gate"] = {
        "citations": [c.n for c in outcome.citations],
        "stripped": list(outcome.stripped_citations),
        "fabricated": list(outcome.fabricated),
        "uncited_claims": outcome.uncited_claims,
        "groundedness": {str(k): v for k, v in outcome.groundedness.items()},
    }
    trace.detail["workflow"] = "ask"
    _record_generation(request, outcome, workflow, "generate")


def _trace_summary(request: AiRequest, outcome: Any, workflow: Any) -> None:
    trace = request.trace
    trace.queries = list(outcome.queries)
    for field_key, retrieval in outcome.retrievals:
        _record_candidates(request, retrieval, stage=f"rerank:{field_key}")
    trace.timings_ms.update(outcome.timings_ms)
    trace.calls.extend(outcome.calls)
    trace.detail["workflow"] = "summary"
    trace.detail["summary"] = {
        "document_id": outcome.document_id,
        "template": outcome.template,
        "fields": len(outcome.fields),
        "fields_present": outcome.fields_present,
        "absent": list(outcome.absent_fields),
        "per_field": list(outcome.per_field),
    }
    trace.detail["gate"] = {
        "citations": [c.n for c in outcome.citations],
        "stripped": list(outcome.stripped_citations),
        "fabricated": list(outcome.fabricated),
    }
    if outcome.coverage is not None:
        trace.detail["coverage"] = outcome.coverage.model_dump(by_alias=False)


def _trace_draft(request: AiRequest, outcome: Any, workflow: Any) -> None:
    trace = request.trace
    trace.queries = list(outcome.queries)
    _record_candidates(request, outcome.retrieval)
    trace.timings_ms.update(outcome.timings_ms)
    trace.calls.extend(outcome.calls)
    trace.detail["workflow"] = "draft"
    trace.detail["draft"] = {
        "draft_type": outcome.draft_type,
        "title": outcome.title,
        "sections": [s.key for s in outcome.sections],
        "uncited_sections": list(outcome.uncited_sections),
    }
    trace.detail["gate"] = {
        "citations": [c.n for c in outcome.citations],
        "stripped": list(outcome.stripped_citations),
        "fabricated": list(outcome.fabricated),
    }


def _record_candidates(request: AiRequest, retrieval: Any, *, stage: str = "rerank") -> None:
    """Every candidate the reranker scored, kept and rejected alike.

    The rejected rows are the point: a trace listing only the survivors cannot
    distinguish "the clause was never retrieved" from "the clause was retrieved
    and scored 0.24 against a floor of 0.25", and those have different fixes.
    """
    if retrieval is None:
        return
    for kept, hits in ((True, retrieval.hits), (False, getattr(retrieval, "rejected", []))):
        for hit in hits:
            request.trace.record_candidate(
                span_id=hit.chunk.span_id,
                document_id=hit.chunk.document_id,
                page=hit.chunk.page_from,
                score=hit.relevance,
                kept=kept,
                stage=stage,
            )


def _record_generation(request: AiRequest, outcome: Any, workflow: Any, stage: str) -> None:
    """The generation call, net of the rewrite already recorded separately."""
    total = outcome.usage
    rewrite_usage = outcome.rewrite.usage if outcome.rewrite is not None else None
    input_tokens = total.input_tokens - (rewrite_usage.input_tokens if rewrite_usage else 0)
    output_tokens = total.output_tokens - (rewrite_usage.output_tokens if rewrite_usage else 0)
    if input_tokens <= 0 and output_tokens <= 0:
        return
    from app.ai.orchestration.intent import Usage as CallUsage
    from app.ai.orchestration.workflows import generation as gen

    net = CallUsage(input_tokens=max(0, input_tokens), output_tokens=max(0, output_tokens))
    request.trace.record_call(
        stage,
        model=workflow.model_name,
        prompt_version=workflow.prompt_version,
        input_tokens=net.input_tokens,
        output_tokens=net.output_tokens,
        usd=gen.usd(net, workflow.model_name),
        latency_ms=total.latency_ms,
    )


# -------------------------------------------------------------- availability


def unavailable_reason(settings: Any | None = None) -> str | None:
    """Why this engine cannot serve a turn, or None when it can.

    Checked rather than attempted, because the alternative is discovering it on
    the first turn — after the user has typed a question and the route has
    already committed two message rows. `/readyz` reports this string, and
    `engine.get_engine()` uses it to resolve `LL_AI_ENGINE=auto`.
    """
    from importlib.util import find_spec

    from app.ai.orchestration.settings import get_router_settings

    cfg = settings or get_router_settings()
    missing = [
        name
        for name, spec in (
            ("langchain-anthropic", "langchain_anthropic"),
            ("qdrant-client", "qdrant_client"),
            ("FlagEmbedding", "FlagEmbedding"),
        )
        if find_spec(spec) is None
    ]
    if missing:
        return f"{', '.join(missing)} not installed (uv sync --extra ai --extra retrieval)"
    if not cfg.has_credentials:
        return "no Anthropic credentials (set ANTHROPIC_API_KEY or LL_AI_API_KEY)"
    return None


def raise_if_unavailable() -> None:
    reason = unavailable_reason()
    if reason is not None:
        raise AiUnavailable(f"The AI layer is not configured: {reason}")


__all__ = [
    "OrchestratedEngine",
    "raise_if_unavailable",
    "unavailable_reason",
]
