"""The summary workflow. Schema-first extraction, one retrieval per field.

    classify (the ingestion type) -> template
      -> per field: retrieve (bge-m3 + Qdrant RRF) -> rerank -> assemble
      -> extract (Sonnet, structured output) -> citation gate
      -> coverage

The event sequence is the M3 contract, unchanged, because the frontend and
`turn_service.TurnAccumulator` were built against it:

    stage(classifying) -> stage(field_retrieval) -> citation* -> excerpt*
      -> stage(mapping) -> summary_start
      -> summary_field* -> [coverage] -> stage(verifying) -> usage -> done

Four decisions worth knowing before changing anything in here.

**One retrieval per field, not one retrieval per document.** A single top-10
over "summarise this agreement" returns ten passages about whatever the
document talks about most, and the nine template fields are then answered from
whichever of them happen to be relevant — so the fields the document mentions
once (assignment, governing law) get answered from passages about something
else, or not at all. Retrieving per field with the field's own query is what
makes a 312-page agreement summarisable at all. It costs N searches and N
generation calls; see `_budget` for what stops that being unbounded.

**Citations are numbered once, across the whole summary.** Every field's
retrieval contributes to one shared source list, so citation `[[7]]` means the
same passage in the Termination field and in the Liability field. Numbering per
field would be cheaper, but the summary card renders one citation list under
nine fields and the attorney clicks `[[7]]` without knowing which field it came
from. `_SourceLedger` owns that numbering.

**An absent field is a filled field.** FR-16: the model returns
`present=false`, and the field is emitted with `value_md=None` rather than
skipped. A skipped field is indistinguishable in the UI from a field that is
still streaming, and "the agreement has no liability cap" is the single most
valuable thing a summary can say.

**Coverage is reported, not estimated.** FR-17: `pages_covered` counts the
distinct pages that retrieval actually drew on, against the document's real
page count. It is deliberately not "we read the whole document", because this
topology does not read the whole document — it reads what N field queries
retrieved, and saying otherwise would be the one claim in the product that is
not checkable.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.ai.disclaimers import disclaimer_for
from app.ai.orchestration import context as context_mod
from app.ai.orchestration.citations import CitationGate
from app.ai.orchestration.intent import Usage as CallUsage
from app.ai.orchestration.prompt import IntentPrompt, load_named_prompt
from app.ai.orchestration.retrieval.pipeline import RetrievalResult, Retriever
from app.ai.orchestration.retrieval.ports import IndexedChunk
from app.ai.orchestration.settings import RouterSettings, get_router_settings
from app.ai.orchestration.templates import (
    TemplateField,
    normalise_doc_type,
    template_for,
    template_name,
)
from app.ai.orchestration.workflows import generation as gen
from app.ai.orchestration.workflows.stages import StageClock
from app.schemas.common import (
    ApiError,
    Citation,
    Coverage,
    DocType,
    Excerpt,
    Refusal,
    RefusalSuggestion,
    Usage,
    Versions,
)
from app.schemas.envelope import SummaryField
from app.schemas.events import (
    CitationEvent,
    CoverageEvent,
    DoneEvent,
    ErrorEvent,
    ExcerptEvent,
    RefusalEvent,
    StreamEvent,
    SummaryFieldEvent,
    SummaryHeader,
    SummaryStartEvent,
    UsageEvent,
)

log = logging.getLogger("legallens.ai.summary")

PROMPT_NAME = "summary_extraction"

_HUMAN_TEMPLATE = """\
{sources}

Document: {document_name} ({document_type})

The field to extract:
<field>
{field_label}
</field>

Extract it from the passages above."""


# The docstring is sent to Claude as the schema description, so it is one line.
# Field order is load-bearing: Claude's structured output follows declaration
# order, so `reasoning` is generated before `present` and `present` before
# `value_md`. That ordering is the mechanism behind FR-16 — the model commits to
# "is this in the passages at all" *before* it starts writing a value, which is
# much harder to walk back than deciding it after a paragraph is already
# written. Reversed, the model writes a plausible clause and then marks it
# present because it has one in front of it.
class FieldExtraction(BaseModel):
    """One template field, extracted from retrieved passages."""

    reasoning: str = Field(
        description="One sentence, under 25 words: what in the passages addresses this field, "
        "or that nothing does."
    )
    present: bool = Field(
        description="True only if the passages actually address this field. False means absent."
    )
    value_md: str | None = Field(
        default=None,
        description="The field's content in Markdown, with [[n]] citations. Null when absent.",
    )
    citations: list[int] = Field(
        default_factory=list,
        description="The source numbers cited in value_md. Empty when absent.",
    )
    flag: Literal["off_market", "unusual"] | None = Field(
        default=None, description="Set only when the passages show something notable."
    )
    flag_note: str | None = Field(
        default=None, description="One clause saying why, when flag is set."
    )


@dataclass
class SummaryOutcome:
    """Everything the turn needs after the stream finishes.

    Passed in by the caller rather than returned, for the reason `AskOutcome`
    is: an async generator cannot return a value, and the turn still has to be
    persisted when the client disconnects mid-summary (NFR-7) — which on a
    nine-field summary of a 312-page agreement is a real scenario, not a
    theoretical one.
    """

    document_id: str = ""
    document_name: str = ""
    document_type: DocType = "other"
    template: str = ""
    fields: list[SummaryField] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    excerpts: list[Excerpt] = field(default_factory=list)
    coverage: Coverage | None = None
    refused: bool = False
    refusal: Refusal | None = None
    usage: CallUsage = field(default_factory=CallUsage)
    #: Per-field: retrieval counts, whether it was present, what the gate did.
    per_field: list[dict[str, Any]] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    #: `(field key, result)` per field that retrieved anything. Paired rather
    #: than a bare list so the trace can say *which* field's query produced a
    #: candidate — nine field queries over one document return overlapping
    #: passages, and nine unattributed rows for the same span cannot be read.
    retrievals: list[tuple[str, RetrievalResult]] = field(default_factory=list)
    stripped_citations: list[int] = field(default_factory=list)
    fabricated: list[str] = field(default_factory=list)
    absent_fields: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def fields_present(self) -> int:
        return sum(1 for f in self.fields if f.value_md)


class _SourceLedger:
    """One citation numbering across every field's retrieval.

    Deduped on `chunk_id`, so a clause retrieved by three different field
    queries — the liability cap turns up under liability, under unusual terms
    and under termination — is one source with one number rather than three
    identical chips. Insertion order is citation order, which means source
    numbers rise as the summary fills, and the number an attorney clicks never
    changes meaning mid-stream.
    """

    def __init__(self) -> None:
        self._by_chunk: dict[str, int] = {}
        self.chunks: list[IndexedChunk] = []

    def add(self, chunk: IndexedChunk) -> int:
        """The source number for `chunk`, assigning one if it is new."""
        found = self._by_chunk.get(chunk.chunk_id)
        if found is not None:
            return found
        self.chunks.append(chunk)
        n = len(self.chunks)
        self._by_chunk[chunk.chunk_id] = n
        return n

    def __len__(self) -> int:
        return len(self.chunks)


class SummaryWorkflow:
    """Schema-driven document summary. FR-15, FR-16, FR-17."""

    def __init__(
        self,
        *,
        retriever: Retriever,
        settings: RouterSettings | None = None,
        chat_model: Any | None = None,
        prompt: IntentPrompt | None = None,
    ) -> None:
        self.settings = settings or get_router_settings()
        self.retriever = retriever
        self.prompt = prompt or load_named_prompt(PROMPT_NAME)
        self.model_name = self.settings.summary_model
        self.prompt_version = self.prompt.version
        self._model = chat_model if chat_model is not None else self._build_model()

    def _build_model(self) -> Any:
        return gen.build_chat_model(
            self.settings,
            model=self.settings.summary_model,
            max_tokens=self.settings.summary_max_tokens,
            timeout=self.settings.summary_timeout_s,
        )

    # ------------------------------------------------------------------ run

    async def run(
        self,
        *,
        document_id: str,
        document_name: str,
        document_type: str,
        document_pages: int = 0,
        question: str = "",
        trace_id: str = "",
        outcome: SummaryOutcome | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream the summary. `outcome` is filled in as it progresses."""
        result = outcome if outcome is not None else SummaryOutcome()
        clock = StageClock()
        fields = template_for(document_type)

        result.document_id = document_id
        result.document_name = document_name
        result.document_type = normalise_doc_type(document_type)
        result.template = template_name(document_type)

        async for event in clock.run("classifying", f"{document_type} · {result.template}"):
            yield event

        # ------------------------------------------------- per-field retrieval
        #
        # Retrieval for every field happens up front, before any generation.
        # Two reasons. The citation and excerpt events have to precede the
        # fields that cite them, or the card renders a chip pointing at a source
        # the client has not been sent. And a document with nothing retrievable
        # at all should refuse before a single generation token is bought.
        ledger = _SourceLedger()
        per_field: list[tuple[TemplateField, list[int], RetrievalResult | None]] = []
        budget = self._budget(len(fields))

        for template_field in fields[:budget]:
            retrieval = await self._retrieve(template_field, document_id)
            result.queries.append(template_field.query)
            if retrieval is None:
                per_field.append((template_field, [], None))
                continue
            result.retrievals.append((template_field.key, retrieval))
            result.timings_ms.update(
                {f"{template_field.key}_{k}": v for k, v in retrieval.timings_ms.items()}
            )
            numbers = [ledger.add(c) for c in retrieval.context]
            per_field.append((template_field, numbers, retrieval))

        async for event in clock.run(
            "field_retrieval",
            f"{len(per_field)} targeted queries · {len(ledger)} distinct sources",
        ):
            yield event

        # ------------------------------------------------------- no support
        if not ledger.chunks:
            refusal = self._refusal(document_name, result.retrievals)
            result.refused = True
            result.refusal = refusal
            yield RefusalEvent(refusal=refusal)
            yield self._usage_event(result, trace_id)
            yield DoneEvent(disclaimer=disclaimer_for("summary"))
            return

        gate = CitationGate(
            ledger.chunks,
            groundedness_floor=self.settings.groundedness_floor,
            strip_ungrounded=self.settings.strip_ungrounded_citations,
        )
        citations = gen.citations_for(ledger.chunks)
        result.citations = citations
        result.excerpts = _excerpts(per_field, ledger)

        for citation in citations:
            yield CitationEvent(citation=citation)
        for excerpt in result.excerpts:
            yield ExcerptEvent(excerpt=excerpt)

        async for event in clock.run(
            "mapping", f"{len(fields)} fields · {self.model_name} per field"
        ):
            yield event

        coverage = self._coverage(result.retrievals, document_pages, len(per_field))
        result.coverage = coverage

        # The header precedes any field, so the card can render its template
        # name and document while the fields stream in (NFR-3).
        yield SummaryStartEvent(
            summary=SummaryHeader(
                document_id=document_id,
                document_name=document_name,
                document_type=result.document_type,
                template_name=result.template,
                coverage=coverage,
            ),
            field_count=len(per_field),
        )

        # ---------------------------------------------------------- extract
        total = len(per_field)
        for i, (template_field, numbers, retrieval) in enumerate(per_field):
            try:
                extracted, usage = await self._extract(
                    template_field, numbers, ledger, document_name, document_type
                )
            except Exception as exc:  # noqa: BLE001 — one field must not lose the rest
                log.exception("extracting %s failed", template_field.key)
                # FR-16 again, from the other direction: a field whose
                # extraction *failed* is not an absent field. Saying "not
                # addressed" because the provider timed out would be a false
                # negative about the document, which is exactly the class of
                # error this product refuses to make. So the turn stops and
                # says what happened, keeping the fields already emitted.
                yield ErrorEvent(
                    error=ApiError(
                        kind="llm_unavailable",
                        message=(f"The summary stopped at “{template_field.label}”: {exc}"),
                        action=(
                            "Retry. The fields already shown are complete, and the "
                            "retrieved passages are in the evidence panel."
                        ),
                        excerpts_available=bool(result.excerpts),
                    )
                )
                return

            result.usage = gen.add(result.usage, usage)
            call = {
                "stage": f"summary:{template_field.key}",
                "model": self.model_name,
                "prompt": self.prompt_version,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "usd": round(gen.usd(usage, self.model_name), 6),
            }
            result.calls.append(call)

            summary_field = self._to_field(template_field, extracted, gate, result)
            result.fields.append(summary_field)
            if not summary_field.value_md:
                result.absent_fields.append(template_field.key)
            result.per_field.append(
                {
                    "key": template_field.key,
                    "query": template_field.query,
                    "sources": len(numbers),
                    "candidates": retrieval.candidate_count if retrieval else 0,
                    "present": bool(summary_field.value_md),
                    "citations": list(summary_field.citations),
                    "flag": summary_field.flag,
                }
            )
            yield SummaryFieldEvent(field=summary_field, index=i, total=total)

        if coverage is not None:
            yield CoverageEvent(coverage=coverage)

        verify_detail = (
            f"{result.fields_present} of {total} fields supported · "
            f"{len(result.citations)} citations resolved"
        )
        if result.absent_fields:
            verify_detail += f" · {len(result.absent_fields)} reported absent"
        if result.stripped_citations:
            verify_detail += f" · {len(result.stripped_citations)} stripped as unresolvable"
        async for event in clock.run("verifying", verify_detail):
            yield event

        result.timings_ms.update(clock.timings)
        yield self._usage_event(result, trace_id)
        yield DoneEvent(disclaimer=disclaimer_for("summary"))

    # ------------------------------------------------------------ internals

    def _budget(self, field_count: int) -> int:
        """How many fields may be retrieved and extracted.

        A cap, because the cost of a summary is linear in the field count and
        the templates are fixed: nine fields is nine searches and nine calls
        against NFR-3's three-minute budget and NFR-8's per-summary spend. The
        default is above every template's length, so it changes nothing today
        and exists so a longer template added later cannot quietly double the
        bill.
        """
        return min(field_count, self.settings.summary_max_fields)

    async def _retrieve(
        self, template_field: TemplateField, document_id: str
    ) -> RetrievalResult | None:
        """One field's retrieval, scoped to the one document being summarised.

        Run in a thread: `Retriever.retrieve` is synchronous and CPU-bound —
        bge-m3 and the cross-encoder are torch forward passes — so calling it
        directly on the event loop would block every other in-flight turn for
        the duration of N field searches. `asyncio.to_thread` releases the loop;
        torch releases the GIL inside the forward pass, so this is real
        concurrency rather than a cosmetic await.
        """
        try:
            return await asyncio.to_thread(
                self.retriever.retrieve, template_field.query, document_ids=[document_id]
            )
        except Exception:  # noqa: BLE001 — one field's retrieval must not lose the rest
            log.exception("retrieval failed for field %s", template_field.key)
            return None

    async def _extract(
        self,
        template_field: TemplateField,
        numbers: list[int],
        ledger: _SourceLedger,
        document_name: str,
        document_type: str,
    ) -> tuple[FieldExtraction, CallUsage]:
        """One field, from only the sources its own retrieval found.

        The model sees this field's passages and no others, numbered with their
        *global* ledger numbers. Sending the whole ledger would be cheaper to
        assemble and much worse: nine fields' passages in one prompt invites the
        model to answer the liability field from the termination clause, which
        is the cross-contamination the per-field topology exists to avoid.
        """
        from langchain_core.prompts import ChatPromptTemplate

        chunks = [ledger.chunks[n - 1] for n in numbers]
        assembled = context_mod.assemble(chunks)
        # `assemble` renumbers from 1, so the numbering is rewritten to the
        # ledger's. Without this the model cites [[1]] meaning this field's
        # first source, the gate resolves [[1]] against the ledger's first
        # source, and every citation in every field after the first points at
        # the wrong clause.
        sources = _renumber(assembled.text, numbers)

        structured = self._model.with_structured_output(
            FieldExtraction, method="json_schema", include_raw=True
        )
        template = ChatPromptTemplate.from_messages(
            [("system", "{system}"), ("human", _HUMAN_TEMPLATE)]
        )
        messages = template.format_messages(
            system=self.prompt.system,
            sources=sources,
            document_name=document_name,
            document_type=document_type,
            field_label=template_field.label,
        )
        raw = await structured.ainvoke(messages)

        parsed = raw.get("parsed") if isinstance(raw, dict) else None
        message = raw.get("raw") if isinstance(raw, dict) else None
        usage = gen.usage_from_meta(getattr(message, "usage_metadata", None) or {})
        if parsed is None or not isinstance(parsed, FieldExtraction):
            detail = raw.get("parsing_error") if isinstance(raw, dict) else None
            raise ValueError(f"unparseable extraction: {detail or 'no parse returned'}")
        return parsed, usage

    def _to_field(
        self,
        template_field: TemplateField,
        extracted: FieldExtraction,
        gate: CitationGate,
        result: SummaryOutcome,
    ) -> SummaryField:
        """Run the gate over one field's value and build the wire shape.

        The gate runs per field rather than once over the assembled summary,
        because FR-22's consequence differs: a fabricated authority inside one
        field voids *that field*, and voiding a nine-field summary over one bad
        sentence would throw away eight good ones. So the field is emitted with
        its value replaced by a statement of what happened, and the turn
        continues.
        """
        if not extracted.present or not extracted.value_md:
            # FR-16: emitted, not skipped. `value_md=None` is what the UI
            # renders as "not addressed", and it is a finding.
            return SummaryField(
                key=template_field.key,
                label=template_field.label,
                value_md=None,
                citations=[],
            )

        checked = gate.check(extracted.value_md)
        if checked.must_refuse:
            findings = [str(f) for f in checked.fabricated]
            result.fabricated.extend(findings)
            log.error("FR-22 in field %s: %s", template_field.key, "; ".join(findings))
            return SummaryField(
                key=template_field.key,
                label=template_field.label,
                value_md=(
                    "*Withheld.* The extracted text cited authority that does not appear "
                    "in this document, so it has not been shown. The passages retrieved "
                    "for this field are in the evidence panel."
                ),
                citations=[],
                flag="unusual",
                flag_note="Extraction withheld by the citation gate (FR-22).",
            )

        result.stripped_citations.extend(
            n for n in checked.stripped if n not in result.stripped_citations
        )
        # Trust the markers in the text over the model's own `citations` list:
        # the list is a second assertion about the same thing and the two
        # disagree often enough that one has to win. The text is what the
        # attorney reads and what the gate verified.
        cited = checked.cited or [n for n in extracted.citations if 1 <= n <= len(gate.sources)]
        return SummaryField(
            key=template_field.key,
            label=template_field.label,
            value_md=checked.text.strip(),
            citations=cited,
            flag=extracted.flag if template_field.flaggable else None,
            flag_note=extracted.flag_note if template_field.flaggable and extracted.flag else None,
        )

    def _coverage(
        self,
        retrievals: list[tuple[str, RetrievalResult]],
        document_pages: int,
        passes: int,
    ) -> Coverage | None:
        """FR-17: what the summary actually drew on.

        `pages_covered` is the count of distinct pages appearing in retrieved
        context, which is a fact rather than an estimate. `passes` is the number
        of field queries, because that is what a "pass" is in this topology.
        """
        if document_pages <= 0:
            return None
        pages: set[int] = set()
        for _, retrieval in retrievals:
            for chunk in retrieval.context:
                pages.update(range(chunk.page_from, max(chunk.page_from, chunk.page_to) + 1))
        covered = len([p for p in pages if 1 <= p <= document_pages])
        return Coverage(
            pages_covered=covered,
            total_pages=document_pages,
            passes=passes,
            note=(
                f"{passes} targeted retrievals drew on {covered} of {document_pages} pages. "
                "Pages no field query retrieved were not read; a term appearing only on "
                "those pages would be reported as absent."
            ),
        )

    def _refusal(
        self, document_name: str, retrievals: list[tuple[str, RetrievalResult]]
    ) -> Refusal:
        """FR-14 for a summary: nothing was retrievable, so nothing is claimed."""
        results = [r for _, r in retrievals]
        candidates = sum(r.candidate_count for r in results)
        best = max(
            (r.best_relevance for r in results if r.best_relevance is not None), default=None
        )
        if candidates == 0:
            message = (
                f"Nothing could be retrieved from “{document_name}”. The document may not "
                "be indexed yet, or its text may not have extracted."
            )
        else:
            message = (
                f"{candidates} passages were retrieved from “{document_name}”, but none was "
                "a close enough match to any template field to summarise from. Rather than "
                "produce a summary out of weakly-related text, Legal Lens is declining."
            )
        return Refusal(
            kind="below_threshold" if candidates else "no_support",
            message=message,
            looked_for=[],
            best_relevance=best,
            threshold=self.retriever.relevance_floor,
            suggestions=[
                RefusalSuggestion(
                    kind="rephrase",
                    label="Ask about a specific clause instead",
                    detail="A targeted question retrieves where a whole-document template did not.",
                ),
                RefusalSuggestion(
                    kind="upload",
                    label="Check the document finished processing",
                    detail="A document still indexing has nothing to retrieve.",
                ),
            ],
        )

    def _usage_event(self, result: SummaryOutcome, trace_id: str) -> UsageEvent:
        u = result.usage
        return UsageEvent(
            usage=Usage(
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                usd=gen.usd(u, self.model_name),
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


def _renumber(sources: str, numbers: list[int]) -> str:
    """Rewrite `assemble`'s 1..N numbering to the ledger's numbers.

    `assemble` always numbers from 1, and it has to: the citation gate resolves
    `[[n]]` positionally against the list it was built with. Here the gate holds
    the *whole ledger* while each prompt holds one field's slice of it, so the
    slice's `n="1"` must be rendered as its ledger number before the model ever
    sees it.
    """
    for position, ledger_number in enumerate(numbers, start=1):
        sources = sources.replace(f'<source n="{position}"', f'<source n="{ledger_number}"', 1)
    return sources


def _excerpts(
    per_field: list[tuple[TemplateField, list[int], RetrievalResult | None]],
    ledger: _SourceLedger,
) -> list[Excerpt]:
    """One excerpt per ledger source, in ledger order.

    Relevance comes from the best field retrieval that found it: a clause
    retrieved weakly for one field and strongly for another is as relevant as
    its best hit, and showing the weaker number would understate the evidence
    the summary actually rests on.

    A ledger entry is usually a *parent*, because that is what expansion put in
    context, and a parent carries no score of its own — the reranker only ever
    scores children. So a parent inherits the best score among the children that
    expanded into it, looked up by `(document_id, parent_key)` for the reason
    `pipeline._expand` gives: parent keys are numbered per document, so a bare
    key collides across documents and a clause from one document would be shown
    with another's relevance.
    """
    best_child: dict[str, float] = {}
    best_parent: dict[tuple[str, str], float] = {}
    parents: dict[tuple[str, str], IndexedChunk] = {}
    for _, _, retrieval in per_field:
        if retrieval is None:
            continue
        for hit in retrieval.hits:
            chunk = hit.chunk
            best_child[chunk.chunk_id] = max(best_child.get(chunk.chunk_id, 0.0), hit.relevance)
            if chunk.parent_key:
                key = (chunk.document_id, chunk.parent_key)
                best_parent[key] = max(best_parent.get(key, 0.0), hit.relevance)
        for chunk in retrieval.context:
            if chunk.is_parent and chunk.parent_key:
                parents[(chunk.document_id, chunk.parent_key)] = chunk

    out: list[Excerpt] = []
    for chunk in ledger.chunks:
        key = (chunk.document_id, chunk.parent_key or "")
        relevance = best_child.get(chunk.chunk_id)
        if relevance is None:
            relevance = best_parent.get(key)
        if relevance is None:
            # Neither the chunk nor its parent key was scored. Reachable only
            # for a chunk that reached context without a hit, which the pipeline
            # does not currently produce; 0.0 rather than an invented midpoint,
            # because a relevance meter is read as a measurement.
            relevance = 0.0
        out.append(
            gen.excerpt_for(
                chunk,
                relevance,
                # A parent is its own context, so it gets none: `excerpt_for`
                # skips a parent that is the chunk itself.
                parent=parents.get(key),
            )
        )
    return out
