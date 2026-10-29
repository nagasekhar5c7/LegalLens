"""The draft workflow. Section-at-a-time composition over chosen sources.

    retrieve over the attorney's selected documents -> rerank -> expand
      -> assemble as fenced data
      -> per section: generate (Sonnet) -> citation gate
      -> verify -> DRAFT_MARKING

The event sequence is the M3 contract, unchanged, because the frontend and
`turn_service.TurnAccumulator` were built against it:

    stage(retrieving) -> stage(reranking) -> citation* -> excerpt*
      -> stage(composing) -> draft_start
      -> draft_section* -> stage(verifying) -> usage -> done

Four decisions worth knowing before changing anything in here.

**Sources are the attorney's choice, not the router's.** FR-19: a draft is built
from documents the attorney selected, and `draft_params.document_ids` is that
selection. It is not widened to the workspace, ever — a memo that silently drew
on a document the attorney did not pick is work-product they cannot account
for. The route refuses the request outright when the selection is empty, so by
the time this workflow runs there is always at least one source document.

**One retrieval for the whole draft, not one per section.** The opposite of the
summary workflow, and for a reason: a memo's sections are five views of *one*
question, so they need the same passages — the Short Answer and the Discussion
both rest on the assignment clause. Retrieving per section would return the
same passages five times under five numberings, and the shared numbering is
what lets the memo cite `[[2]]` consistently from Short Answer through
Conclusion. The query is the question presented, which is what the draft is
about.

**Sections are generated in order and see what came before.** Each call
receives the sections already written, because a Conclusion that does not know
what the Discussion concluded restates the Short Answer, and a Discussion that
cannot see the Statement of Facts re-states the facts. It costs context — the
preceding sections are re-sent each time — and buys a document that reads as
one piece. `settings.draft_history_chars` caps what is carried.

**A fabricated citation voids the whole draft, not one section.** The opposite
of the summary workflow's per-field containment, and deliberately so. A summary
is nine independent findings and losing one leaves eight useful ones. A memo is
one argument: a Discussion withheld for citing an invented case leaves a Short
Answer asserting a conclusion whose support has been removed, which reads as
finished work and is worse than no memo. So FR-22 here halts the turn and
refuses.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.ai.disclaimers import disclaimer_for
from app.ai.orchestration import context as context_mod
from app.ai.orchestration.citations import CitationGate
from app.ai.orchestration.intent import Usage as CallUsage
from app.ai.orchestration.prompt import IntentPrompt, load_named_prompt
from app.ai.orchestration.retrieval.pipeline import RetrievalResult, Retriever
from app.ai.orchestration.retrieval.ports import IndexedChunk
from app.ai.orchestration.settings import RouterSettings, get_router_settings
from app.ai.orchestration.templates import (
    DraftSectionPlan,
    draft_type_label,
    normalise_draft_type,
    plan_for,
)
from app.ai.orchestration.workflows import generation as gen
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
from app.schemas.envelope import DraftParams, DraftSection
from app.schemas.events import (
    CitationEvent,
    DoneEvent,
    DraftHeader,
    DraftSectionEvent,
    DraftStartEvent,
    ErrorEvent,
    ExcerptEvent,
    RefusalEvent,
    StreamEvent,
    UsageEvent,
)

log = logging.getLogger("legallens.ai.draft")

PROMPT_NAME = "draft_generation"

_HUMAN_TEMPLATE = """\
{sources}

You are writing one section of a {type_label}.

{brief}
{written_block}
The section to write now:
<section name="{heading}">
{instruction}
</section>

Write that section only. No heading."""


@dataclass
class DraftOutcome:
    """Everything the turn needs after the stream finishes."""

    draft_type: str = "memo"
    title: str = ""
    params: DraftParams | None = None
    sections: list[DraftSection] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    excerpts: list[Excerpt] = field(default_factory=list)
    refused: bool = False
    refusal: Refusal | None = None
    retrieval: RetrievalResult | None = None
    usage: CallUsage = field(default_factory=CallUsage)
    queries: list[str] = field(default_factory=list)
    stripped_citations: list[int] = field(default_factory=list)
    fabricated: list[str] = field(default_factory=list)
    uncited_sections: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)


class DraftWorkflow:
    """Grounded drafting from the attorney's selected sources. FR-18 to FR-23."""

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
        self.model_name = self.settings.draft_model
        self.prompt_version = self.prompt.version
        self._model = chat_model if chat_model is not None else self._build_model()

    def _build_model(self) -> Any:
        return gen.build_chat_model(
            self.settings,
            model=self.settings.draft_model,
            max_tokens=self.settings.draft_max_tokens,
            timeout=self.settings.draft_timeout_s,
        )

    # ------------------------------------------------------------------ run

    async def run(
        self,
        *,
        params: DraftParams,
        question: str = "",
        document_names: dict[str, str] | None = None,
        trace_id: str = "",
        outcome: DraftOutcome | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream the draft. `outcome` is filled in as it progresses."""
        result = outcome if outcome is not None else DraftOutcome()
        clock = StageClock()

        draft_type = normalise_draft_type(params.draft_type)
        plan = plan_for(draft_type)
        type_label = draft_type_label(draft_type)
        # FR-19 again, defensively. The route refuses an empty selection before
        # the engine is reached, and this must not depend on it remembering to:
        # retrieving over `[]` with no guard would mean a draft over nothing,
        # and `Retriever.retrieve` correctly returns empty for an empty scope,
        # so the failure would surface as a confusing refusal rather than as the
        # policy decision it is.
        document_ids = list(params.document_ids)

        result.draft_type = draft_type
        result.params = params

        # The query is what the draft is *about*. The attorney's framing when
        # they gave one, because that is the most precise statement of the
        # question available; the turn text otherwise.
        query = (params.question_presented or question).strip() or type_label
        result.queries.append(query)

        async for event in clock.run(
            "retrieving",
            f"bge-m3 dense + sparse, RRF · top {self.retriever.candidate_k} over "
            f"{len(document_ids)} selected document{'s' if len(document_ids) != 1 else ''}",
        ):
            yield event

        try:
            retrieval = await asyncio.to_thread(
                self.retriever.retrieve, query, document_ids=document_ids
            )
        except Exception as exc:  # noqa: BLE001
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
        async for event in clock.run(
            "reranking",
            f"{len(retrieval.hits)} of {retrieval.candidate_count} above floor {floor}"
            if retrieval.hits
            else f"nothing above floor {floor}",
        ):
            yield event

        # ------------------------------------------------------- no support
        if retrieval.found_nothing:
            refusal = self._refusal(query, retrieval, document_ids, document_names or {})
            result.refused = True
            result.refusal = refusal
            yield RefusalEvent(refusal=refusal)
            yield self._usage_event(result, trace_id)
            yield DoneEvent(disclaimer=disclaimer_for("draft"))
            return

        assembled = context_mod.assemble(retrieval.context)
        gate = CitationGate(
            assembled.chunks,
            groundedness_floor=self.settings.groundedness_floor,
            strip_ungrounded=self.settings.strip_ungrounded_citations,
        )
        citations = gen.citations_for(assembled.chunks)
        result.citations = citations
        result.excerpts = _excerpts(retrieval)

        for citation in citations:
            yield CitationEvent(citation=citation)
        for excerpt in result.excerpts:
            yield ExcerptEvent(excerpt=excerpt)

        async for event in clock.run(
            "composing",
            f"{len(plan)} sections · {assembled.source_count} sources · "
            f"~{assembled.token_estimate} tokens, fenced as data",
        ):
            yield event

        result.title = self._title(draft_type, params, document_names or {})
        yield DraftStartEvent(
            draft=DraftHeader(draft_type=draft_type, title=result.title, params=params)
        )

        # ---------------------------------------------------------- compose
        brief = _brief(params, question, type_label)
        written: list[DraftSection] = []
        for plan_section in plan:
            try:
                body, usage = await self._compose(
                    plan_section, assembled, brief, type_label, written
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("composing %s failed", plan_section.key)
                yield ErrorEvent(
                    error=ApiError(
                        kind="llm_unavailable",
                        message=f"The draft stopped at “{plan_section.heading}”: {exc}",
                        action=(
                            "Retry. A partial draft is not usable work-product, so nothing "
                            "already written is being presented as complete."
                        ),
                        excerpts_available=bool(result.excerpts),
                    )
                )
                return

            result.usage = gen.add(result.usage, usage)
            result.calls.append(
                {
                    "stage": f"draft:{plan_section.key}",
                    "model": self.model_name,
                    "prompt": self.prompt_version,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "usd": round(gen.usd(usage, self.model_name), 6),
                }
            )

            checked = gate.check(body)
            if checked.must_refuse:
                # FR-22, and the whole draft goes. See the module docstring for
                # why this differs from the summary workflow's containment.
                findings = [str(f) for f in checked.fabricated]
                result.fabricated = findings
                result.refused = True
                log.error("FR-22 halting draft at %s: %s", plan_section.key, "; ".join(findings))
                result.refusal = Refusal(
                    kind="no_support",
                    message=(
                        f"The draft was stopped while writing “{plan_section.heading}”, "
                        "which cited authority that does not appear in the documents you "
                        "selected. Legal Lens will not emit a draft containing a citation "
                        "it cannot resolve to one of your sources, and a partial memo is "
                        "not usable work-product — so nothing is being shown."
                    ),
                    looked_for=[query],
                    suggestions=[
                        RefusalSuggestion(
                            kind="upload",
                            label="Add the authority as a source",
                            detail=(
                                "The draft needs a case or statute that is not among the "
                                "selected documents. Upload it and select it as a source."
                            ),
                        ),
                        RefusalSuggestion(
                            kind="rephrase",
                            label="Narrow the question to the documents' own terms",
                            detail=(
                                "A question answerable from the sources can be drafted from them."
                            ),
                        ),
                    ],
                )
                async for event in clock.run("verifying", f"halted: {'; '.join(findings)}"):
                    yield event
                yield RefusalEvent(refusal=result.refusal)
                yield self._usage_event(result, trace_id)
                yield DoneEvent(disclaimer=disclaimer_for("draft"))
                return

            result.stripped_citations.extend(
                n for n in checked.stripped if n not in result.stripped_citations
            )
            if not checked.cited and not plan_section.citation_optional:
                # FR-20: recorded rather than enforced. A section that genuinely
                # states only that the sources are silent has nothing to cite,
                # and refusing it would make the honest answer the one that
                # fails. The count is what calibrates the prompt at M5.
                result.uncited_sections.append(plan_section.key)

            section = DraftSection(
                key=plan_section.key,
                heading=plan_section.heading,
                body_md=checked.text.strip(),
                citations=checked.cited,
            )
            result.sections.append(section)
            written.append(section)
            yield DraftSectionEvent(section=section)

        verify_detail = f"{len(result.citations)} citations resolved to source spans"
        if result.stripped_citations:
            verify_detail += f" · {len(result.stripped_citations)} stripped as unresolvable"
        if result.uncited_sections:
            verify_detail += f" · {len(result.uncited_sections)} section(s) uncited"
        verify_detail += " · no citation outside the source set (FR-22)"
        async for event in clock.run("verifying", verify_detail):
            yield event

        result.timings_ms.update(clock.timings)
        yield self._usage_event(result, trace_id)
        yield DoneEvent(disclaimer=disclaimer_for("draft"))

    # ------------------------------------------------------------ internals

    async def _compose(
        self,
        plan_section: DraftSectionPlan,
        assembled: context_mod.AssembledContext,
        brief: str,
        type_label: str,
        written: list[DraftSection],
    ) -> tuple[str, CallUsage]:
        """One section. Not streamed to the user — see `generation.complete`."""
        from langchain_core.prompts import ChatPromptTemplate

        template = ChatPromptTemplate.from_messages(
            [("system", "{system}"), ("human", _HUMAN_TEMPLATE)]
        )
        messages = template.format_messages(
            system=self.prompt.system,
            sources=assembled.text,
            type_label=type_label,
            brief=brief,
            written_block=self._written_block(written),
            heading=plan_section.heading,
            instruction=plan_section.instruction,
        )
        return await gen.complete(self._model, messages)

    def _written_block(self, written: list[DraftSection]) -> str:
        """The sections already written, so the document reads as one piece.

        Truncated per section from the *end*, not the start: what a later
        section needs from an earlier one is its conclusion, and the conclusion
        is at the bottom. Truncating from the front would feed the Conclusion
        the Discussion's preamble and withhold its holding.
        """
        if not written:
            return ""
        cap = self.settings.draft_history_chars
        parts: list[str] = []
        for section in written:
            body = section.body_md.strip()
            if len(body) > cap:
                body = "…" + body[-cap:].lstrip()
            parts.append(f"## {section.heading}\n{body}")
        return (
            "Sections already written, for continuity — do not repeat or revise them:\n"
            "<written>\n" + "\n\n".join(parts) + "\n</written>\n"
        )

    def _title(self, draft_type: str, params: DraftParams, document_names: dict[str, str]) -> str:
        """A title from what is known, never from the model.

        A separate call to title the document would cost a round trip on the
        latency path for a string the attorney will rewrite anyway, and a title
        generated from the sources is one more unverified assertion in a
        document whose whole premise is that every assertion is verified.
        """
        label = draft_type_label(draft_type)
        names = [document_names[d] for d in params.document_ids if d in document_names]
        if names:
            shown = names[0] if len(names) == 1 else f"{names[0]} and {len(names) - 1} other"
            return f"{label} — {shown}"
        return label

    def _refusal(
        self,
        query: str,
        retrieval: RetrievalResult,
        document_ids: list[str],
        document_names: dict[str, str],
    ) -> Refusal:
        """FR-14 for a draft, naming the sources the attorney actually chose."""
        if not document_ids:
            scope = "no source documents"
        elif len(document_ids) == 1:
            scope = f"“{document_names.get(document_ids[0], document_ids[0])}”"
        else:
            scope = f"the {len(document_ids)} documents you selected"

        if retrieval.candidate_count == 0:
            message = (
                f"Nothing could be retrieved from {scope} for this question. The documents "
                "may not cover it, or they may not be indexed yet."
            )
        else:
            message = (
                f"{retrieval.candidate_count} passages were retrieved from {scope}, but none "
                "was a close enough match to draft from. A draft assembled out of weakly-"
                "related text would read as work-product while resting on nothing, so "
                "Legal Lens is declining to write it."
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
                    label="Select more source documents",
                    detail="The question may be answered by a document not among the sources.",
                ),
                RefusalSuggestion(
                    kind="rephrase",
                    label="State the question in the documents' own terms",
                    detail="Defined terms retrieve better than paraphrases.",
                ),
            ],
        )

    def _usage_event(self, result: DraftOutcome, trace_id: str) -> UsageEvent:
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


def _brief(params: DraftParams, question: str, type_label: str) -> str:
    """The attorney's instructions, as the model sees them.

    Their framing is carried verbatim and fenced, because it *is* the brief —
    and because the same rule applies to it as to a source: an attorney pasting
    a client email into `question_presented` is pasting untrusted text.
    """
    lines = [f"This is a {type_label}."]
    asked = (params.question_presented or question).strip()
    if asked:
        lines.append(f"The question the attorney posed:\n<brief>\n{asked}\n</brief>")
    if params.audience:
        lines.append(f"Audience: {params.audience}.")
    if params.tone:
        lines.append(f"Register: {params.tone}.")
    if params.position:
        # Carried, and bounded. An attorney may ask for the case for a position;
        # they may not ask for support that the sources do not contain, and the
        # system prompt's grounding rules are not negotiable by this field.
        lines.append(
            f"The attorney asks that the draft develop this position: {params.position}. "
            "Develop it only so far as the sources support; where they do not, say so."
        )
    return "\n".join(lines)


def _excerpts(retrieval: RetrievalResult) -> list[Excerpt]:
    """FR-12 for a draft: the passages it was built from, with context."""
    parents: dict[tuple[str, str], IndexedChunk] = {
        (c.document_id, c.parent_key): c for c in retrieval.context if c.is_parent and c.parent_key
    }
    return [
        gen.excerpt_for(
            hit.chunk,
            hit.relevance,
            parent=parents.get((hit.chunk.document_id, hit.chunk.parent_key or "")),
        )
        for hit in retrieval.hits
    ]
