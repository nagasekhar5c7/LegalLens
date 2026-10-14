"""The M3 implementation of `AiEngine`.

Its only job is to emit the right events in the right order with plausible
timings. It holds no retrieval logic and must never acquire any: the moment it
does, it becomes a second AI layer that M4 has to delete. `tests/test_layering.py`
keeps the boundary honest from the other side.

Ported from `frontend/src/mocks/stream.ts`, including its event ordering, its
jitter, and its ~600 ms time-to-first-token — so the streaming UI that was built
against the mock is exercised by the real backend without changing.
"""

from __future__ import annotations

import asyncio
import random
import re
import secrets
import time
from collections.abc import AsyncIterator, Callable

from app.ai.disclaimers import disclaimer_for
from app.ai.fixtures.answers import (
    OUT_OF_SCOPE_REFUSAL,
    ScriptedAnswer,
    find_scripted,
    refusal_for,
)
from app.ai.fixtures.corpus import citation_label, short_doc_name
from app.ai.fixtures.drafts import draft_for, draft_spans
from app.ai.fixtures.pages import PLANTED, resolve_span, span_context
from app.ai.fixtures.summaries import SUMMARY_SPANS, summary_for
from app.ai.ports import AiRequest
from app.ai.scenarios import SCENARIO_ERRORS, SLOW_FACTOR
from app.config import get_settings
from app.schemas.common import (
    ApiError,
    Citation,
    Coverage,
    Excerpt,
    Refusal,
    Stage,
    StageKey,
    Usage,
    Versions,
)
from app.schemas.envelope import DraftParams, DraftSection, SummaryField
from app.schemas.events import (
    CitationEvent,
    CoverageEvent,
    DoneEvent,
    DraftHeader,
    DraftSectionEvent,
    DraftStartEvent,
    ErrorEvent,
    ExcerptEvent,
    IntentEvent,
    RefusalEvent,
    ScopeEvent,
    StageEvent,
    StreamEvent,
    SummaryFieldEvent,
    SummaryHeader,
    SummaryStartEvent,
    TokenEvent,
    UsageEvent,
)

STAGE_LABEL: dict[StageKey, str] = {
    "routing": "Routing the question",
    "classifying": "Resolving document type",
    "retrieving": "Hybrid retrieval",
    "field_retrieval": "Retrieving per template field",
    "reranking": "Cross-encoder rerank",
    "expanding": "Parent-document expansion",
    "assembling": "Assembling context",
    "mapping": "Extracting fields",
    "composing": "Composing sections",
    "writing": "Writing",
    "verifying": "Verifying citations",
}

#: The reranker's score floor. Below it, nothing is returned and the engine
#: refuses rather than assembling an answer from weakly-related text (FR-14).
RELEVANCE_FLOOR = 0.25

_CITE_MARKER = re.compile(r"\[\[(\d+)\]\]")

#: The stage-emitting helper each flow is handed. Declared once so the flows
#: can be annotated without repeating the signature.
StageFn = Callable[..., AsyncIterator[StreamEvent]]

# `summar` is a prefix, not a word: a trailing \b would never match
# "summarise" or "summary". The mock had the same regex and was never exercised
# on that input, which is exactly the sort of thing the contract tests exist to
# catch.
_DRAFT_RE = re.compile(r"\b(draft|memo|memorandum|brief|write me|prepare a)\w*", re.I)
_SUMMARY_RE = re.compile(
    r"\b(summar\w*|overview|what(?:'s| is) in|walk me through|key terms|brief me)", re.I
)
_OUT_OF_SCOPE_RE = re.compile(r"\b(weather|recipe|stock price|who won|translate)\w*", re.I)


def _jitter(ms: float, spread: float = 0.4) -> float:
    """So the stream never looks metronomic."""
    factor = get_settings().stream_delay_factor
    return ms * factor * (1 - spread / 2 + random.random() * spread)  # noqa: S311 — cosmetic


class FixtureEngine:
    name = "fixture-engine/m3"
    prompt_version = "ask/v4.2.1"
    model_version = "claude-opus-5"
    index_version = "fixtures:legallens-m3 (2026-10-02)"

    # ------------------------------------------------------------- routing
    async def route_intent(self, request: AiRequest) -> str:
        """Keyword heuristics standing in for the LLM classifier.

        Async only to satisfy the port, which went async when routing became a
        real model call. Nothing in here awaits.

        An explicit mode chip wins outright — the architecture makes the router
        overridable, and an invisible decision the user cannot override is the
        fastest way to lose trust in a tool whose pitch is transparency.
        """
        if request.mode != "auto":
            return request.mode
        text = request.text
        if _DRAFT_RE.search(text):
            return "draft"
        if _SUMMARY_RE.search(text):
            return "summary"
        if _OUT_OF_SCOPE_RE.search(text):
            return "out_of_scope"
        return "ask"

    # ------------------------------------------------------- public ports
    async def answer(self, request: AiRequest) -> AsyncIterator[StreamEvent]:
        """Answer the question, or refuse it as outside the product's scope.

        `out_of_scope` is reachable from here rather than being a fourth port
        method: "this is not something Legal Lens does" is an outcome of asking,
        not a separate workflow (NFR-14 keeps the AI layer to three callables).
        """
        intent = "out_of_scope" if _OUT_OF_SCOPE_RE.search(request.text) else "ask"
        async for event in self._run(request, forced_intent=intent):
            yield event

    async def summarize(self, request: AiRequest) -> AsyncIterator[StreamEvent]:
        async for event in self._run(request, forced_intent="summary"):
            yield event

    async def draft(self, request: AiRequest) -> AsyncIterator[StreamEvent]:
        async for event in self._run(request, forced_intent="draft"):
            yield event

    async def run(self, request: AiRequest) -> AsyncIterator[StreamEvent]:
        """Entry point for `mode='auto'`: routes, then dispatches."""
        async for event in self._run(request, forced_intent=None):
            yield event

    # ------------------------------------------------------------ the stream
    async def _run(
        self, request: AiRequest, *, forced_intent: str | None
    ) -> AsyncIterator[StreamEvent]:
        slow = SLOW_FACTOR if request.scenario == "slow" else 1.0
        t0 = time.perf_counter()
        scope = request.scope_used

        def elapsed() -> float:
            return (time.perf_counter() - t0) * 1000

        async def stage(
            key: StageKey, ms: float, detail: str | None = None
        ) -> AsyncIterator[StreamEvent]:
            active = Stage(
                key=key,
                label=STAGE_LABEL[key],
                detail=detail,
                status="active",
                elapsed_ms=elapsed(),
            )
            yield StageEvent(stage=active)
            await asyncio.sleep(_jitter(ms * slow) / 1000)
            yield StageEvent(
                stage=Stage(
                    key=key,
                    label=STAGE_LABEL[key],
                    detail=detail,
                    status="done",
                    elapsed_ms=elapsed(),
                )
            )

        intent = forced_intent or await self.route_intent(request)
        if request.scenario == "refusal":
            intent = "ask"

        async for event in stage(
            "routing", 180, "mode: auto" if request.mode == "auto" else f"mode: {request.mode}"
        ):
            yield event
        yield IntentEvent(intent=intent, mode=request.mode)
        yield ScopeEvent(scope_used=scope)

        # Nothing retrievable in scope: refuse before spending a token.
        if scope.document_count == 0:
            excluded = len(scope.excluded)
            if excluded:
                plural_was = " was" if excluded == 1 else "s were"
                plural_is = "it is" if excluded == 1 else "they are"
                message = (
                    f"No document in scope is ready to search. {excluded} document"
                    f"{plural_was} excluded because {plural_is} still processing or "
                    "failed to parse."
                )
            else:
                message = "No documents are in scope, so there is nothing to search."
            yield RefusalEvent(
                refusal=Refusal(
                    kind="out_of_scope",
                    message=message,
                    looked_for=[],
                    suggestions=[
                        {"kind": "widen_scope", "label": "Search the whole workspace"},
                        {"kind": "upload", "label": "Upload a document"},
                    ],
                )
            )
            async for event in self._finish(0.0004, t0, slow, "answer"):
                yield event
            return

        if intent == "summary":
            async for event in self._summary_flow(request, stage, t0, slow):
                yield event
            return
        if intent == "draft":
            async for event in self._draft_flow(request, stage, t0, slow):
                yield event
            return
        if intent == "out_of_scope":
            async for event in stage("retrieving", 120, "out of scope — no retrieval run"):
                yield event
            yield RefusalEvent(refusal=Refusal.model_validate(OUT_OF_SCOPE_REFUSAL))
            async for event in self._finish(0.0008, t0, slow, "answer"):
                yield event
            return

        async for event in self._ask_flow(request, stage, t0, slow):
            yield event

    # ------------------------------------------------------------- ask flow
    async def _ask_flow(
        self, request: AiRequest, stage: StageFn, t0: float, slow: float
    ) -> AsyncIterator[StreamEvent]:
        scripted: ScriptedAnswer | None = (
            None if request.scenario == "refusal" else find_scripted(request.text)
        )
        span_ids = list(scripted.span_ids) if scripted else []
        # FR-7/FR-9: a fixture span whose document is out of scope must not be
        # cited. Scope is the backend's call, not the fixture's.
        in_scope = set(request.scope_used.document_ids)
        span_ids = [s for s in span_ids if _span_document(s) in in_scope]
        if scripted and not span_ids:
            scripted = None

        citations = _citations(span_ids, request)
        excerpts = _excerpts(span_ids, request, scripted.relevances if scripted else None)

        async for event in stage(
            "retrieving", 480, f"{max(18, len(span_ids) * 13)} candidates · BM25 + dense, RRF"
        ):
            yield event
        async for event in stage(
            "reranking",
            360,
            f"{len(span_ids)} above floor {RELEVANCE_FLOOR}"
            if scripted
            else f"nothing above floor {RELEVANCE_FLOOR}",
        ):
            yield event

        if not scripted:
            # The excerpt list is empty here by construction, but emitting
            # whatever retrieval did find before refusing is the honest order.
            for excerpt in excerpts:
                yield ExcerptEvent(excerpt=excerpt)
            yield RefusalEvent(
                refusal=Refusal.model_validate(refusal_for(request.text, _describe_scope(request)))
            )
            async for event in self._finish(0.0121, t0, slow, "answer"):
                yield event
            return

        async for event in stage("expanding", 200, "child chunks → parent sections"):
            yield event
        for excerpt in excerpts:
            yield ExcerptEvent(excerpt=excerpt)
            await asyncio.sleep(_jitter(55 * slow) / 1000)
        async for event in stage(
            "assembling", 140, "retrieved text fenced as data, not instructions"
        ):
            yield event

        # Errors land after retrieval — which is exactly why excerpts survive
        # an outage (NFR-6).
        forced = SCENARIO_ERRORS.get(request.scenario or "")
        if forced:
            await asyncio.sleep(_jitter(1800 if request.scenario == "timeout" else 420) / 1000)
            yield ErrorEvent(error=ApiError.model_validate(forced))
            return

        writing = Stage(
            key="writing",
            label=STAGE_LABEL["writing"],
            status="active",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )
        yield StageEvent(stage=writing)

        emitted: set[int] = set()
        chunks = _tokenize(scripted.answer_md)
        interrupt_at = int(len(chunks) * 0.38) if request.scenario == "interrupted" else -1

        for i, chunk in enumerate(chunks):
            # Citations arrive mid-stream, as the claim needing them is written.
            for match in _CITE_MARKER.finditer(chunk):
                n = int(match.group(1))
                if n not in emitted and n <= len(citations):
                    emitted.add(n)
                    yield CitationEvent(citation=citations[n - 1])

            if i == interrupt_at:
                yield TokenEvent(text=chunk)
                # NFR-7: the route persists what arrived as `incomplete`.
                yield ErrorEvent(
                    error=ApiError(
                        kind="network",
                        message=(
                            "The response stream was interrupted. What is shown above is partial."
                        ),
                        action="Retry to regenerate the full answer.",
                    )
                )
                return

            yield TokenEvent(text=chunk)
            await asyncio.sleep(_jitter(14 if i < 4 else 22) * slow / 1000)

        yield StageEvent(
            stage=Stage(
                key="writing",
                label=STAGE_LABEL["writing"],
                status="done",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )
        )
        async for event in stage(
            "verifying", 300, f"{len(citations)} citations resolved to source spans"
        ):
            yield event
        async for event in self._finish(0.0309, t0, slow, "answer"):
            yield event

    # --------------------------------------------------------- summary flow
    async def _summary_flow(
        self, request: AiRequest, stage: StageFn, t0: float, slow: float
    ) -> AsyncIterator[StreamEvent]:
        doc_id = (
            request.draft_params.document_ids[0]
            if request.draft_params and request.draft_params.document_ids
            else (request.scope_used.document_ids[0] if request.scope_used.document_ids else None)
        )
        document = request.document(doc_id or "") if doc_id else None
        if document is None:
            document = next(iter(request.ready_documents), None)
        if document is None:
            yield RefusalEvent(
                refusal=Refusal(
                    kind="out_of_scope",
                    message="No document is in scope to summarise.",
                    suggestions=[{"kind": "upload", "label": "Upload a document"}],
                )
            )
            async for event in self._finish(0.0004, t0, slow, "summary"):
                yield event
            return

        payload = summary_for(document.id, document.name, document.type)
        span_ids = SUMMARY_SPANS.get(document.id, [])
        citations = _citations(span_ids, request)
        excerpts = _excerpts(span_ids, request, None)
        fields = [SummaryField.model_validate(f) for f in payload["fields"]]

        async for event in stage("classifying", 300, f"{document.type} · detected"):
            yield event
        async for event in stage("field_retrieval", 520, f"{len(fields)} targeted queries"):
            yield event
        for citation in citations:
            yield CitationEvent(citation=citation)
        for excerpt in excerpts:
            yield ExcerptEvent(excerpt=excerpt)
            await asyncio.sleep(_jitter(40 * slow) / 1000)
        async for event in stage("mapping", 260, "per-section extraction"):
            yield event

        # The header goes out before any field, so the summary card can render
        # its template and document name while the fields stream in.
        yield SummaryStartEvent(
            summary=SummaryHeader(
                document_id=payload["document_id"],
                document_name=payload["document_name"],
                document_type=payload["document_type"],
                template_name=payload["template_name"],
                coverage=Coverage.model_validate(payload["coverage"])
                if payload.get("coverage")
                else None,
            ),
            field_count=len(fields),
        )

        forced = SCENARIO_ERRORS.get(request.scenario or "")
        if forced:
            yield ErrorEvent(error=ApiError.model_validate(forced))
            return

        # Field-by-field, because a 200-page summary is a three-minute wait and
        # a generic spinner would read as a hang (NFR-3).
        for i, field in enumerate(fields):
            await asyncio.sleep(_jitter(380 * slow) / 1000)
            if request.scenario == "interrupted" and i == 3:
                yield ErrorEvent(
                    error=ApiError(
                        kind="network",
                        message="The connection dropped mid-summary.",
                        action="Retry to regenerate the remaining fields.",
                    )
                )
                return
            yield SummaryFieldEvent(field=field, index=i, total=len(fields))

        if payload.get("coverage"):
            yield CoverageEvent(coverage=Coverage.model_validate(payload["coverage"]))
        async for event in stage("verifying", 320, f"{len(citations)} citations resolved"):
            yield event
        async for event in self._finish(0.1842, t0, slow, "summary"):
            yield event

    # ----------------------------------------------------------- draft flow
    async def _draft_flow(
        self, request: AiRequest, stage: StageFn, t0: float, slow: float
    ) -> AsyncIterator[StreamEvent]:
        params = request.draft_params or DraftParams(
            draft_type="memo",
            question_presented=request.text,
            audience="supervising partner",
            position="",
            tone="neutral predictive",
            document_ids=list(request.scope_used.document_ids),
        )
        payload = draft_for(params.model_dump())
        span_ids = draft_spans(params.draft_type)
        in_scope = set(request.scope_used.document_ids)
        span_ids = [s for s in span_ids if _span_document(s) in in_scope]
        citations = _citations(span_ids, request)
        excerpts = _excerpts(span_ids, request, None)
        sections = [DraftSection.model_validate(s) for s in payload["sections"]]

        async for event in stage(
            "retrieving", 620, f"{max(10, len(span_ids) * 5)} candidates · hybrid"
        ):
            yield event
        async for event in stage(
            "reranking", 340, f"top {len(span_ids)} above floor {RELEVANCE_FLOOR}"
        ):
            yield event
        for citation in citations:
            yield CitationEvent(citation=citation)
        for excerpt in excerpts:
            yield ExcerptEvent(excerpt=excerpt)
            await asyncio.sleep(_jitter(40 * slow) / 1000)
        async for event in stage("composing", 280, f"{len(sections)} sections"):
            yield event

        yield DraftStartEvent(
            draft=DraftHeader(
                draft_type=payload["draft_type"],
                title=payload["title"],
                params=params,
            )
        )

        forced = SCENARIO_ERRORS.get(request.scenario or "")
        if forced:
            yield ErrorEvent(error=ApiError.model_validate(forced))
            return

        for i, section in enumerate(sections):
            await asyncio.sleep(_jitter(520 * slow) / 1000)
            if request.scenario == "interrupted" and i == 2:
                yield ErrorEvent(
                    error=ApiError(
                        kind="network",
                        message="The connection dropped mid-draft.",
                        action="Retry to regenerate the remaining sections.",
                    )
                )
                return
            yield DraftSectionEvent(section=section)

        # FR-22: the allowlist check. In M3 the allowlist is the fixture span
        # set, so this is a tautology — but the stage, the wording and the
        # placement are what M4 slots a real enforcer into.
        async for event in stage("verifying", 420, "no citation outside the source set (FR-22)"):
            yield event
        async for event in self._finish(0.2317, t0, slow, "draft"):
            yield event

    # --------------------------------------------------------------- finish
    async def _finish(
        self, usd: float, t0: float, slow: float, kind: str
    ) -> AsyncIterator[StreamEvent]:
        latency = (time.perf_counter() - t0) * 1000
        usage = Usage(
            input_tokens=round(1200 + random.random() * 5400),  # noqa: S311
            output_tokens=round(180 + random.random() * 900),  # noqa: S311
            usd=usd,
            latency_ms=latency,
            time_to_first_token_ms=min(latency, _jitter(640) * slow),
        )
        yield UsageEvent(
            usage=usage,
            versions=Versions(
                model=self.model_version, prompt=self.prompt_version, index=self.index_version
            ),
            trace_id=f"tr_{secrets.token_hex(5)}",
        )
        yield DoneEvent(disclaimer=disclaimer_for(kind))


# ------------------------------------------------------------------ helpers


def _span_document(span_id: str) -> str | None:
    planted = PLANTED.get(span_id)
    return planted.document_id if planted else None


def _citations(span_ids: list[str], request: AiRequest) -> list[Citation]:
    """Derive the citation list from planted spans, so numbering and labels
    agree with the excerpts they came from."""
    out: list[Citation] = []
    for i, span_id in enumerate(span_ids):
        span = resolve_span(span_id)
        planted = PLANTED.get(span_id)
        if span is None or planted is None:
            continue
        document = request.document(planted.document_id)
        name = document.name if document else planted.document_id
        out.append(
            Citation(
                n=i + 1,
                span_id=span_id,
                document_id=planted.document_id,
                document_name=name,
                page=span.page,
                section=span.section,
                label=citation_label(name, span.page, span.section),
            )
        )
    return out


def _excerpts(
    span_ids: list[str], request: AiRequest, relevances: list[float] | None
) -> list[Excerpt]:
    """FR-12: excerpts come from the same spans, with real surrounding context."""
    out: list[Excerpt] = []
    for i, span_id in enumerate(span_ids):
        context = span_context(span_id)
        span = resolve_span(span_id)
        planted = PLANTED.get(span_id)
        if context is None or span is None or planted is None:
            continue
        document = request.document(planted.document_id)
        name = document.name if document else planted.document_id
        relevance = (
            relevances[i] if relevances and i < len(relevances) else max(0.42, 0.93 - i * 0.09)
        )
        out.append(
            Excerpt(
                span_id=span_id,
                document_id=planted.document_id,
                document_name=name,
                page=span.page,
                section=span.section,
                before=context["before"],
                text=context["text"],
                after=context["after"],
                relevance=relevance,
            )
        )
    return out


def _describe_scope(request: AiRequest) -> str:
    """Mirrors `describeScope` in mocks/stream.ts — the phrase a refusal uses to
    name what was actually searched (FR-14)."""
    scope = request.scope_used
    if scope.document_count == 0:
        return "the selected documents"
    if scope.kind == "document":
        document = request.document(scope.document_ids[0]) if scope.document_ids else None
        return f"“{short_doc_name(document.name, 200)}”" if document else "this document"
    if scope.kind == "workspace":
        return f"your workspace ({scope.document_count} documents, {scope.page_count} pages)"
    return f"the {scope.document_count} selected documents"


def _tokenize(markdown: str) -> list[str]:
    """Chunk into 1–3 word groups, as the mock did, so the stream looks like a
    model writing rather than a typewriter."""
    words = re.findall(r"\S+\s*", markdown)
    out: list[str] = []
    i = 0
    while i < len(words):
        take = 1 + int(random.random() * 3)  # noqa: S311
        out.append("".join(words[i : i + take]))
        i += take
    return out
