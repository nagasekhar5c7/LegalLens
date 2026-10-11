"""The ask workflow. Claude Sonnet 5 over hybrid-retrieved, reranked passages.

    rewrite (Haiku) -> retrieve (bge-m3 + Qdrant RRF) -> rerank (bge-reranker)
      -> expand to parents -> assemble as fenced data -> generate (Sonnet)
      -> citation gate

The event sequence is the M3 contract, unchanged, because the frontend and
`turn_service.TurnAccumulator` were built against it:

    stage(retrieving) -> stage(reranking)
      -> [refusal]
      -> stage(expanding) -> excerpt* -> stage(assembling)
      -> stage(writing) -> token* / citation* -> stage(writing done)
      -> stage(verifying) -> usage -> done

Two decisions in here are worth knowing before changing anything.

**Citations are emitted as the claim needing them is written.** The gate runs at
the end, but the `[[n]]` markers are detected in the token stream, so a citation
chip appears beside the sentence it supports rather than all of them arriving
after the prose. That is the behaviour the M3 stream already had.

**Tokens are released a sentence at a time, not a token at a time.** See
`citations.SentenceGate`: FR-10 wants streaming and FR-22 wants a fabricated
citation never to be emitted, and those conflict unless the stream is gated at
some granularity. A sentence is the smallest unit the authority scanner can
judge. The visible cost is roughly one sentence of lag.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.ai.disclaimers import disclaimer_for
from app.ai.orchestration import context as context_mod
from app.ai.orchestration.citations import CITE_MARKER, CitationGate, SentenceGate
from app.ai.orchestration.intent import Usage as CallUsage
from app.ai.orchestration.prompt import IntentPrompt, load_named_prompt
from app.ai.orchestration.retrieval.pipeline import RetrievalResult, Retriever
from app.ai.orchestration.rewriter import QueryRewriter, RewriteResult
from app.ai.orchestration.settings import RouterSettings, get_router_settings
from app.ai.orchestration.workflows.stages import StageClock
from app.schemas.common import (
    ApiError,
    Citation,
    Excerpt,
    Refusal,
    RefusalSuggestion,
    Usage,
    Versions,
)
from app.schemas.events import (
    CitationEvent,
    DoneEvent,
    ErrorEvent,
    ExcerptEvent,
    RefusalEvent,
    StreamEvent,
    TokenEvent,
    UsageEvent,
)

log = logging.getLogger("legallens.ai.ask")

PROMPT_NAME = "ask_generation"

_HUMAN_TEMPLATE = """\
{sources}

The attorney's question:
<question>
{question}
</question>"""

#: Characters of context either side of an excerpt, so the evidence panel shows
#: the passage in situ rather than a bare fragment (FR-12).
_EXCERPT_PAD = 180


@dataclass
class AskOutcome:
    """Everything the turn needs after the stream finishes.

    Returned alongside the events rather than derived from them: the route
    persists the turn, and re-parsing a token stream to find out what happened
    would be both slow and lossy.
    """

    answer_md: str = ""
    citations: list[Citation] = field(default_factory=list)
    excerpts: list[Excerpt] = field(default_factory=list)
    refused: bool = False
    refusal: Refusal | None = None
    rewrite: RewriteResult | None = None
    retrieval: RetrievalResult | None = None
    usage: CallUsage = field(default_factory=CallUsage)
    stripped_citations: list[int] = field(default_factory=list)
    fabricated: list[str] = field(default_factory=list)
    uncited_claims: int = 0
    groundedness: dict[int, float] = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)


class AskWorkflow:
    """Grounded question answering over the attorney's own documents."""

    def __init__(
        self,
        *,
        retriever: Retriever,
        rewriter: QueryRewriter | None = None,
        settings: RouterSettings | None = None,
        chat_model: Any | None = None,
        prompt: IntentPrompt | None = None,
    ) -> None:
        self.settings = settings or get_router_settings()
        self.retriever = retriever
        self.rewriter = rewriter
        self.prompt = prompt or load_named_prompt(PROMPT_NAME)
        self.model_name = self.settings.ask_model
        self.prompt_version = self.prompt.version
        self._model = chat_model if chat_model is not None else self._build_model()

    def _build_model(self) -> Any:
        from langchain_anthropic import ChatAnthropic

        from app.ai.orchestration.router import RouterUnavailable

        if not self.settings.has_credentials:
            raise RouterUnavailable(
                "No Anthropic credentials. Set ANTHROPIC_API_KEY (or LL_AI_API_KEY)."
            )
        kwargs: dict[str, Any] = {
            "model": self.settings.ask_model,
            "max_tokens": self.settings.ask_max_tokens,
            "timeout": self.settings.ask_timeout_s,
            "max_retries": self.settings.max_retries,
            "api_key": self.settings.api_key,
        }
        # Only sent when explicitly configured. Sampling parameters were
        # removed on the 4.6-and-later models, so passing `temperature` to
        # claude-sonnet-5 returns 400 "temperature is deprecated for this
        # model" — and it fails at generation time, after retrieval has already
        # been paid for.
        if self.settings.ask_temperature is not None:
            kwargs["temperature"] = self.settings.ask_temperature
        return ChatAnthropic(**kwargs)

    # ------------------------------------------------------------------ run

    async def run(
        self,
        question: str,
        *,
        document_ids: list[str],
        history: tuple[tuple[str, str], ...] = (),
        trace_id: str = "",
        scope_label: str = "",
        outcome: AskOutcome | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream the answer. `outcome` is filled in as the stream progresses.

        The caller passes an `AskOutcome` in rather than receiving one back,
        because an async generator cannot return a value and the turn still has
        to be persisted when the client disconnects mid-stream (NFR-7).
        """
        result = outcome if outcome is not None else AskOutcome()
        clock = StageClock()

        # ---------------------------------------------------- pre-retrieval
        rewrite = await self._rewrite(question, history)
        result.rewrite = rewrite
        result.usage = _add(result.usage, rewrite.usage)
        query = rewrite.query

        # ---------------------------------------------------------- retrieve
        detail = f"bge-m3 dense + sparse, RRF · top {self.retriever.candidate_k}"
        if rewrite.rewritten:
            detail = f"{detail} · rewritten: {query!r}"
        async for event in clock.run("retrieving", detail):
            yield event

        try:
            retrieval = self.retriever.retrieve(query, document_ids=document_ids)
        except Exception as exc:  # noqa: BLE001 — retrieval is best-effort here
            log.exception("retrieval failed")
            yield ErrorEvent(
                error=ApiError(
                    kind="llm_unavailable",
                    message=f"Retrieval failed: {exc}",
                    action="Retry. If this persists, the document index may need rebuilding.",
                    excerpts_available=False,
                )
            )
            return

        result.retrieval = retrieval
        result.timings_ms.update(retrieval.timings_ms)

        floor = self.retriever.relevance_floor
        best = retrieval.best_relevance
        async for event in clock.run(
            "reranking",
            f"{len(retrieval.hits)} of {retrieval.candidate_count} above floor {floor}"
            if retrieval.hits
            else f"nothing above floor {floor}"
            + (f" (best {best:.2f})" if best is not None else ""),
        ):
            yield event

        # ------------------------------------------------------- no support
        if retrieval.found_nothing:
            refusal = self._refusal(query, retrieval, scope_label, document_ids)
            result.refused = True
            result.refusal = refusal
            yield RefusalEvent(refusal=refusal)
            yield self._usage_event(result, trace_id)
            yield DoneEvent(disclaimer=disclaimer_for("answer"))
            return

        # ---------------------------------------------------------- expand
        expand_detail = "child chunks → parent sections"
        if retrieval.dropped_parents:
            expand_detail += (
                f" · {retrieval.dropped_parents} dropped at "
                f"{self.retriever.context_token_budget}-token budget"
            )
        async for event in clock.run("expanding", expand_detail):
            yield event

        assembled = context_mod.assemble(retrieval.context)
        result.excerpts = [_excerpt(h.chunk, h.relevance) for h in retrieval.hits]
        for excerpt in result.excerpts:
            yield ExcerptEvent(excerpt=excerpt)

        async for event in clock.run(
            "assembling",
            f"{assembled.source_count} sources · ~{assembled.token_estimate} tokens · "
            "fenced as data, not instructions",
        ):
            yield event

        # -------------------------------------------------------- generate
        gate = CitationGate(
            assembled.chunks,
            groundedness_floor=self.settings.groundedness_floor,
            strip_ungrounded=self.settings.strip_ungrounded_citations,
        )
        sentence_gate = SentenceGate(gate)
        citations = _citations(assembled.chunks)

        yield clock.open("writing", f"{self.model_name} over {assembled.source_count} sources")

        emitted: set[int] = set()
        call_usage = CallUsage()
        try:
            async for chunk_text, usage in self._stream(assembled, rewrite.query):
                if usage is not None:
                    call_usage = usage
                    continue
                for released in sentence_gate.feed(chunk_text):
                    for n in _markers(released):
                        if n not in emitted and 1 <= n <= len(citations):
                            emitted.add(n)
                            yield CitationEvent(citation=citations[n - 1])
                    yield TokenEvent(text=released)
                if sentence_gate.blocked:
                    break
            if not sentence_gate.blocked:
                for released in sentence_gate.flush():
                    for n in _markers(released):
                        if n not in emitted and 1 <= n <= len(citations):
                            emitted.add(n)
                            yield CitationEvent(citation=citations[n - 1])
                    yield TokenEvent(text=released)
        except Exception as exc:  # noqa: BLE001
            log.exception("generation failed")
            yield ErrorEvent(
                error=ApiError(
                    kind="llm_unavailable",
                    message=f"The answer could not be generated: {exc}",
                    action=(
                        "Retry, or read the retrieved passages directly — they are listed "
                        "in the evidence panel."
                    ),
                    excerpts_available=bool(result.excerpts),
                )
            )
            return

        result.usage = _add(result.usage, call_usage)
        yield clock.close("writing")

        # ------------------------------------------------------ verify (FR-21/22)
        checked = gate.check(sentence_gate.released_text)
        result.answer_md = checked.text
        result.stripped_citations = list(checked.stripped)
        result.uncited_claims = checked.uncited_claims
        result.groundedness = dict(checked.groundedness)
        result.citations = [citations[n - 1] for n in checked.cited if 1 <= n <= len(citations)]

        if sentence_gate.blocked:
            # FR-22 tripped mid-stream, so nothing bad was emitted. The turn
            # becomes a refusal: partial text whose next sentence was fabricated
            # must not be presented as an answer (NFR-7).
            findings = [str(f) for f in sentence_gate.blocked_on]
            result.fabricated = findings
            result.refused = True
            result.refusal = Refusal(
                kind="no_support",
                message=(
                    "The answer was stopped because it began citing authority that does "
                    "not appear in the documents in scope. Legal Lens will not emit a "
                    "citation it cannot resolve to a source you supplied."
                ),
                looked_for=[query],
                suggestions=[
                    RefusalSuggestion(
                        kind="rephrase",
                        label="Ask about the documents' own terms",
                        detail="Questions needing outside authority cannot be grounded here.",
                    )
                ],
            )
            async for event in clock.run("verifying", f"halted: {'; '.join(findings)}"):
                yield event
            yield RefusalEvent(refusal=result.refusal)
            yield self._usage_event(result, trace_id)
            yield DoneEvent(disclaimer=disclaimer_for("answer"))
            return

        if checked.must_refuse:
            findings = [str(f) for f in checked.fabricated]
            result.fabricated = findings
            log.error("FR-22 post-hoc: %s", "; ".join(findings))

        verify_detail = f"{len(result.citations)} citations resolved to source spans"
        if checked.stripped:
            verify_detail += f" · {len(checked.stripped)} stripped as unresolvable"
        if checked.uncited_claims:
            verify_detail += f" · {checked.uncited_claims} uncited claims"
        async for event in clock.run("verifying", verify_detail):
            yield event

        result.timings_ms.update(clock.timings)
        yield self._usage_event(result, trace_id)
        yield DoneEvent(disclaimer=disclaimer_for("answer"))

    # ------------------------------------------------------------ internals

    async def _rewrite(self, question: str, history: tuple[tuple[str, str], ...]) -> RewriteResult:
        if self.rewriter is None:
            return RewriteResult(
                query=question.strip(),
                original=question.strip(),
                source="no_history",
                reasoning="Rewriting disabled.",
            )
        return await self.rewriter.arewrite(question, history)

    async def _stream(
        self, assembled: context_mod.AssembledContext, question: str
    ) -> AsyncIterator[tuple[str, CallUsage | None]]:
        """Yields `(text, None)` per chunk, then `("", usage)` once at the end.

        One generator rather than two so the caller cannot forget to collect
        usage, which NFR-8 needs on every call.
        """
        from langchain_core.prompts import ChatPromptTemplate

        template = ChatPromptTemplate.from_messages(
            [("system", "{system}"), ("human", _HUMAN_TEMPLATE)]
        )
        messages = template.format_messages(
            system=self.prompt.system,
            sources=assembled.text,
            question=question,
        )

        total = CallUsage()
        async for part in self._model.astream(messages):
            text = _text_of(part)
            if text:
                yield text, None
            meta = getattr(part, "usage_metadata", None)
            if meta:
                total = _usage_from_meta(meta)
        yield "", total

    def _refusal(
        self,
        query: str,
        retrieval: RetrievalResult,
        scope_label: str,
        document_ids: list[str],
    ) -> Refusal:
        """FR-14: a refusal that names the scope searched and what it found.

        `best_relevance` and the floor both ride on it, because "nothing was
        relevant enough" and "nothing was found at all" are different problems
        and the attorney can act on the difference.
        """
        scope = scope_label or (
            f"{len(document_ids)} document{'s' if len(document_ids) != 1 else ''} in scope"
        )
        if retrieval.candidate_count == 0:
            message = (
                f"Nothing in {scope} matched this question. The documents may not "
                "cover it, or they may not be indexed yet."
            )
        else:
            message = (
                f"{retrieval.candidate_count} passages were retrieved from {scope}, but "
                "none was a close enough match to answer from. Rather than assemble an "
                "answer out of weakly-related text, Legal Lens is declining to answer."
            )
        return Refusal(
            kind="below_threshold" if retrieval.candidate_count else "no_support",
            message=message,
            looked_for=[query],
            best_relevance=retrieval.best_relevance,
            threshold=self.retriever.relevance_floor,
            suggestions=[
                RefusalSuggestion(
                    kind="widen_scope",
                    label="Search the whole workspace",
                    detail="The question may be answered by a document outside the current scope.",
                ),
                RefusalSuggestion(
                    kind="rephrase",
                    label="Use the document's own wording",
                    detail="Defined terms retrieve better than paraphrases.",
                ),
            ],
        )

    def _usage_event(self, result: AskOutcome, trace_id: str) -> UsageEvent:
        u = result.usage
        return UsageEvent(
            usage=Usage(
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                usd=_usd(u, self.model_name),
                latency_ms=u.latency_ms,
                time_to_first_token_ms=result.timings_ms.get("ttft_ms", 0.0),
            ),
            versions=Versions(
                model=self.model_name,
                prompt=self.prompt_version,
                index=self.retriever.store.name,
            ),
            trace_id=trace_id,
        )


# ------------------------------------------------------------------ helpers


def _markers(text: str) -> list[int]:
    return [int(m.group(1)) for m in CITE_MARKER.finditer(text)]


def _text_of(part: Any) -> str:
    """LangChain chunk content is a string or a list of content blocks."""
    content = getattr(part, "content", "")
    if isinstance(content, str):
        return content
    out = []
    for block in content or []:
        if isinstance(block, str):
            out.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            out.append(str(block.get("text", "")))
    return "".join(out)


def _usage_from_meta(meta: dict[str, Any]) -> CallUsage:
    details = meta.get("input_token_details") or {}
    return CallUsage(
        input_tokens=int(meta.get("input_tokens") or 0),
        output_tokens=int(meta.get("output_tokens") or 0),
        cache_read_input_tokens=int(details.get("cache_read") or 0),
        cache_creation_input_tokens=int(details.get("cache_creation") or 0),
    )


def _add(a: CallUsage, b: CallUsage) -> CallUsage:
    return CallUsage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        cache_read_input_tokens=a.cache_read_input_tokens + b.cache_read_input_tokens,
        cache_creation_input_tokens=a.cache_creation_input_tokens + b.cache_creation_input_tokens,
        latency_ms=a.latency_ms + b.latency_ms,
    )


#: Per-MTok input/output, by model. `CallUsage.usd` hardcodes Haiku, which is
#: right for the router but wrong for a turn that also ran Sonnet.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
}


def _usd(usage: CallUsage, model: str) -> float:
    """Approximate: the rewrite ran on Haiku and generation on Sonnet, and these
    totals are summed. Attributed at the generation model's rate, which is the
    dominant term by an order of magnitude. Exact per-call attribution is a
    `UsageTransaction` row per call at integration, not an engine concern."""
    rate_in, rate_out = _PRICES.get(model, (2.00, 10.00))
    return (
        usage.input_tokens * rate_in
        + usage.cache_read_input_tokens * rate_in * 0.1
        + usage.cache_creation_input_tokens * rate_in * 1.25
        + usage.output_tokens * rate_out
    ) / 1_000_000


def _excerpt(chunk: Any, relevance: float) -> Excerpt:
    text = chunk.text.strip()
    return Excerpt(
        span_id=chunk.span_id,
        document_id=chunk.document_id,
        document_name=chunk.document_name,
        page=chunk.page_from,
        section=chunk.section_path,
        before="",
        text=text[: _EXCERPT_PAD * 4],
        after="",
        relevance=round(relevance, 3),
    )


def _citations(chunks: list[Any]) -> list[Citation]:
    return [
        Citation(
            n=i,
            span_id=c.span_id,
            document_id=c.document_id,
            document_name=c.document_name,
            page=c.page_from,
            section=c.section_path,
            label=c.citation_label,
        )
        for i, c in enumerate(chunks, start=1)
    ]
