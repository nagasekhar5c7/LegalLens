"""The adapter between the backend and the orchestration layer.

The workflow tests check that each workflow emits the right events. This file
checks the seam around them, which is where integration bugs actually live:

  * the turn's envelope — `stage(routing)`, `intent`, `scope` — which belongs to
    the engine and not to any workflow, and which `TurnAccumulator` needs in
    order to persist a turn the thread can re-render;
  * routing happening **once** per turn, with `answer()` reading the decision
    rather than paying for a second classification;
  * the three things decided before a workflow runs: empty scope, out-of-scope,
    and which document a summary is of;
  * `TurnTrace` being filled from facts, and filled even when the client
    disconnects mid-stream — the turn whose trace matters most, because the
    record is all anyone will ever see of it.

Everything runs offline: a fake router, a fake retriever and a fake chat model.
No model is downloaded and no API is called.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessageChunk

from app.ai.orchestrated_engine import OrchestratedEngine, unavailable_reason
from app.ai.orchestration import (
    AskWorkflow,
    DraftWorkflow,
    Hit,
    IntentDecision,
    Retriever,
    RouterSettings,
    SummaryWorkflow,
    Usage,
    load_named_prompt,
)
from app.ai.orchestration.workflows.summary import FieldExtraction
from app.ai.ports import AiRequest, ScopedDocument
from app.schemas.common import ScopeUsed
from app.schemas.envelope import DraftParams
from tests.test_retrieval import FakeEmbedder, FakeReranker, FakeStore, chunk

CAP_TEXT = "Liability under this Agreement is capped at twelve (12) months of fees."


# ----------------------------------------------------------------- doubles


class FakeRouter:
    """Records what it was asked and returns a preset decision."""

    def __init__(self, intent: str = "ask", *, source: str = "model", error: str | None = None):
        self.intent = intent
        self.source = source
        self.error = error
        self.calls: list[object] = []

    async def aclassify(self, request):  # noqa: ANN001, ANN201
        self.calls.append(request)
        return IntentDecision(
            intent=self.intent,  # type: ignore[arg-type]
            confidence=0.91,
            rationale="The request names a clause and asks what it says.",
            source=self.source,  # type: ignore[arg-type]
            usage=Usage(input_tokens=420, output_tokens=38, latency_ms=310.0),
            error=self.error,
            model="claude-haiku-4-5",
            prompt_version="intent/v1+abcd1234",
            trace_id=getattr(request, "trace_id", None),
        )


class FakeChat:
    def __init__(self, text: str = "The cap is twelve months of fees.[[1]]") -> None:
        self.text = text
        self.calls = 0

    async def astream(self, messages):  # noqa: ANN001, ANN201, ARG002
        self.calls += 1
        for word in self.text.split(" "):
            yield AIMessageChunk(content=word + " ")
        yield AIMessageChunk(
            content="",
            usage_metadata={
                "input_tokens": 8000,
                "output_tokens": 95,
                # Required by `AIMessageChunk`: a dict without it fails
                # validation inside LangChain, which surfaces as a generation
                # error rather than as a bad test double.
                "total_tokens": 8095,
            },
        )


class FakeStructuredChat:
    def __init__(self, extraction: FieldExtraction | None = None) -> None:
        self.extraction = extraction or FieldExtraction(
            reasoning="present", present=True, value_md=f"{CAP_TEXT}[[1]]", citations=[1]
        )

    def with_structured_output(self, schema, **kwargs):  # noqa: ANN001, ANN201, ARG002
        return self

    async def ainvoke(self, messages):  # noqa: ANN001, ANN201, ARG002
        class _Msg:
            usage_metadata = {"input_tokens": 3000, "output_tokens": 60}

        return {"parsed": self.extraction, "raw": _Msg(), "parsing_error": None}


def _settings(**over: object) -> RouterSettings:
    base: dict[str, object] = {"api_key": "sk-ant-test"}
    base.update(over)
    return RouterSettings(**base)  # type: ignore[arg-type]


def _retriever(
    *, hits: list[Hit] | None = None, floor: float = 0.25, scores: dict[str, float] | None = None
) -> Retriever:
    store = FakeStore(
        hits if hits is not None else [Hit(chunk(1, text=CAP_TEXT, parent="p1", doc=1), 0.9)],
        [chunk(1, text=CAP_TEXT, parent="p1", is_parent=True, doc=1)],
    )
    return Retriever(
        store=store,
        embedder=FakeEmbedder(),
        reranker=FakeReranker(scores),
        relevance_floor=floor,
    )


def _engine(
    *,
    router: FakeRouter | None = None,
    retriever: Retriever | None = None,
    chat: FakeChat | None = None,
    structured: FakeStructuredChat | None = None,
    **over: object,
) -> OrchestratedEngine:
    """An engine with every expensive part replaced.

    The private attributes are the intended seam: they are what the lazy
    accessors cache into, so setting them is exactly equivalent to the first
    turn having built them — and it keeps the engine's own wiring under test
    rather than mocked out.
    """
    settings = _settings(**over)
    engine = OrchestratedEngine(settings)
    shared = retriever or _retriever()
    engine._router = router or FakeRouter()
    engine._retriever_cache = shared
    engine._rewriter = None  # no rewrite call; the ask workflow short-circuits
    engine._ask = AskWorkflow(
        retriever=shared,
        rewriter=None,
        settings=settings,
        chat_model=chat or FakeChat(),
        prompt=load_named_prompt("ask_generation"),
    )
    engine._summary = SummaryWorkflow(
        retriever=shared,
        settings=settings,
        chat_model=structured or FakeStructuredChat(),
        prompt=load_named_prompt("summary_extraction"),
    )
    engine._draft = DraftWorkflow(
        retriever=shared,
        settings=settings,
        chat_model=chat or FakeChat(),
        prompt=load_named_prompt("draft_generation"),
    )
    return engine


def _request(
    text: str = "What is the liability cap?",
    *,
    mode: str = "auto",
    documents: list[ScopedDocument] | None = None,
    kind: str = "workspace",
    draft_params: DraftParams | None = None,
    scenario: str | None = None,
    excluded: list | None = None,
) -> AiRequest:
    docs = (
        documents
        if documents is not None
        else [
            ScopedDocument(
                id="doc1",
                name="Master Services Agreement",
                type="contract",
                pages=312,
                status="ready",
            )
        ]
    )
    ready = [d for d in docs if d.status == "ready"]
    return AiRequest(
        text=text,
        mode=mode,  # type: ignore[arg-type]
        scope_used=ScopeUsed(
            kind=kind,  # type: ignore[arg-type]
            document_ids=[d.id for d in ready],
            document_count=len(ready),
            page_count=sum(d.pages for d in ready),
            excluded=excluded or [],
        ),
        documents=docs,
        conversation_id="conv_1",
        trace_id="tr_test",
        user_id="usr_1",
        draft_params=draft_params,
        scenario=scenario,
    )


async def _drain(stream):  # noqa: ANN001, ANN201
    return [event async for event in stream]


def _types(events) -> list[str]:  # noqa: ANN001
    return [e.type for e in events]


# ------------------------------------------------------------- the port


def test_the_engine_is_constructible_without_credentials_or_models() -> None:
    """`/readyz` constructs one to report on it, on a process that may never
    serve a turn. Construction must not load a 2.2 GB model or take the Qdrant
    directory lock the ingestion worker may be holding."""
    engine = OrchestratedEngine(_settings(api_key=""))
    assert engine.name == "orchestrated/m4"
    assert engine.model_version == "claude-sonnet-5"
    assert "bge-m3" in engine.index_version
    assert engine.prompt_version.startswith("ask/")
    assert engine._retriever_cache is None, "nothing may be built eagerly"


# ---------------------------------------------------------------- routing


async def test_routing_happens_once_and_lands_on_the_trace() -> None:
    router = FakeRouter("ask")
    engine = _engine(router=router)
    request = _request()

    assert await engine.route_intent(request) == "ask"
    await _drain(engine.answer(request))

    assert len(router.calls) == 1, "answer() must not re-classify"
    routing = request.trace.detail["routing"]
    assert routing["intent"] == "ask"
    assert routing["source"] == "model"
    assert routing["confidence"] == 0.91
    assert "names a clause" in routing["rationale"]


async def test_a_model_routing_call_is_costed_on_the_trace() -> None:
    engine = _engine(router=FakeRouter("ask"))
    request = _request()
    await engine.route_intent(request)

    call = next(c for c in request.trace.calls if c["stage"] == "routing")
    assert call["model"] == "claude-haiku-4-5"
    assert call["input_tokens"] == 420
    assert call["usd"] > 0


async def test_an_explicit_mode_chip_costs_nothing_and_says_so() -> None:
    """The attorney pressed a chip, so no classifier ran. The trace must not
    imply one did."""
    engine = _engine()
    request = _request(mode="ask")
    events = await _drain(engine.answer(request))

    routing_stage = next(e for e in events if e.type == "stage" and e.stage.key == "routing")
    assert "router not consulted" in routing_stage.stage.detail
    assert request.trace.calls == [] or all(c["stage"] != "routing" for c in request.trace.calls)


async def test_a_degraded_router_is_recorded_rather_than_hidden() -> None:
    """NFR-6: the turn survives a provider outage, and the trace says the label
    came from a keyword fallback rather than from the model."""
    router = FakeRouter("ask", source="heuristic_fallback", error="APIConnectionError")
    engine = _engine(router=router)
    request = _request()
    await engine.route_intent(request)
    events = await _drain(engine.answer(request))

    assert request.trace.detail["routing"]["error"] == "APIConnectionError"
    stage = next(e for e in events if e.type == "stage" and e.stage.key == "routing")
    assert "degraded" in stage.stage.detail
    assert "heuristic_fallback" in stage.stage.detail


# ------------------------------------------------------- the turn envelope


async def test_every_port_opens_with_routing_intent_and_scope() -> None:
    """The M3 contract's opening, which `TurnAccumulator` reads to set the
    turn's kind and its persisted `scopeUsed`."""
    for port, expected_intent, params in (
        ("answer", "ask", None),
        ("summarize", "summary", None),
        ("draft", "draft", DraftParams(draft_type="memo", document_ids=["doc1"])),
    ):
        engine = _engine()
        request = _request(draft_params=params)
        events = await _drain(getattr(engine, port)(request))
        types = _types(events)

        assert types[0] == "stage", port
        assert events[0].stage.key == "routing", port
        assert types[1] == "stage", port
        assert types[2] == "intent", port
        assert events[2].intent == expected_intent, port
        assert types[3] == "scope", port
        assert events[3].scope_used.document_ids == ["doc1"], port


async def test_the_intent_event_carries_the_requested_mode() -> None:
    engine = _engine()
    request = _request(mode="auto")
    events = await _drain(engine.answer(request))
    intent = next(e for e in events if e.type == "intent")
    assert intent.mode == "auto"


# ---------------------------------------------------- decided before a workflow


@pytest.mark.parametrize("port", ["answer", "summarize", "draft"])
async def test_an_empty_scope_refuses_before_spending_a_token(port: str) -> None:
    """FR-7. Nothing ready in scope means nothing to retrieve, on every port."""
    chat = FakeChat()
    engine = _engine(chat=chat)
    request = _request(documents=[], draft_params=DraftParams(draft_type="memo"))
    events = await _drain(getattr(engine, port)(request))

    assert _types(events) == ["stage", "stage", "intent", "scope", "refusal", "done"]
    refusal = events[4].refusal
    assert refusal.kind == "out_of_scope"
    assert "nothing to search" in refusal.message
    assert chat.calls == 0


async def test_an_excluded_document_is_named_in_the_refusal() -> None:
    """ "No documents in scope" and "the three you selected are still indexing"
    are different problems, and only the second says to wait rather than
    upload."""
    from app.schemas.common import ExcludedDocument

    engine = _engine()
    request = _request(
        documents=[ScopedDocument(id="d1", name="A", type="contract", pages=4, status="indexing")],
        excluded=[ExcludedDocument(document_id="d1", name="A", reason="Still indexing")],
    )
    events = await _drain(engine.answer(request))
    refusal = next(e for e in events if e.type == "refusal").refusal

    assert "1 document was excluded" in refusal.message
    assert "still processing" in refusal.message


async def test_out_of_scope_refuses_without_retrieving() -> None:
    engine = _engine(router=FakeRouter("out_of_scope"))
    request = _request("What is the weather in Delaware?")
    assert await engine.route_intent(request) == "out_of_scope"
    events = await _drain(engine.answer(request))

    assert _types(events) == [
        "stage",
        "stage",
        "intent",
        "scope",
        "stage",
        "stage",
        "refusal",
        "done",
    ]
    assert events[2].intent == "out_of_scope"
    refusal = events[6].refusal
    assert refusal.kind == "out_of_scope"
    assert "Nothing was searched" in refusal.message
    assert "model's own knowledge" in refusal.message
    stage = events[4].stage
    assert stage.key == "retrieving"
    assert stage.detail == "out of scope — no retrieval run"


async def test_an_explicit_ask_is_never_turned_into_an_out_of_scope_refusal() -> None:
    """One deliberate difference from `FixtureEngine`, which keyword-scans the
    text inside `answer()`. A mode chip picks the workflow; whether the corpus
    can support the question is retrieval's finding, and its refusal names what
    was searched and what it scored where a keyword match cannot."""
    engine = _engine()
    request = _request("translate this into French", mode="ask")
    events = await _drain(engine.answer(request))

    assert next(e for e in events if e.type == "intent").intent == "ask"
    assert any(e.type == "stage" and e.stage.key == "retrieving" for e in events)


async def test_a_summary_targets_the_explicitly_selected_document() -> None:
    engine = _engine()
    docs = [
        ScopedDocument(id="d1", name="MSA", type="contract", pages=312, status="ready"),
        ScopedDocument(id="d2", name="Opinion", type="opinion", pages=20, status="ready"),
    ]
    request = _request(
        documents=docs, draft_params=DraftParams(draft_type="memo", document_ids=["d2"])
    )
    events = await _drain(engine.summarize(request))
    start = next(e for e in events if e.type == "summary_start")

    assert start.summary.document_id == "d2"
    assert start.summary.document_type == "opinion"
    assert start.summary.template_name == "Judicial opinion template"


async def test_a_summary_falls_back_to_the_first_document_in_scope() -> None:
    engine = _engine()
    docs = [
        ScopedDocument(id="d1", name="MSA", type="contract", pages=312, status="ready"),
        ScopedDocument(id="d2", name="Opinion", type="opinion", pages=20, status="ready"),
    ]
    events = await _drain(engine.summarize(_request(documents=docs)))
    assert next(e for e in events if e.type == "summary_start").summary.document_id == "d1"


async def test_a_summary_never_targets_a_document_that_is_not_ready() -> None:
    """FR-7 from the summary side: an explicit selection of a still-indexing
    document must not be summarised from a half-built index."""
    engine = _engine()
    docs = [
        ScopedDocument(id="d1", name="MSA", type="contract", pages=312, status="ready"),
        ScopedDocument(id="d2", name="Pending", type="contract", pages=9, status="indexing"),
    ]
    request = _request(
        documents=docs, draft_params=DraftParams(draft_type="memo", document_ids=["d2"])
    )
    events = await _drain(engine.summarize(request))
    assert next(e for e in events if e.type == "summary_start").summary.document_id == "d1"


async def test_a_draft_without_params_defaults_from_the_turn() -> None:
    """Reachable through `/api/conversations/{id}/messages`, where the client
    sends a mode but no draft params. The sources default to the resolved
    scope, never to nothing — FR-19 is the route's refusal, not a silent empty
    draft here."""
    engine = _engine()
    request = _request("Draft a memo on the cap.", draft_params=None)
    events = await _drain(engine.draft(request))
    start = next(e for e in events if e.type == "draft_start")

    assert start.draft.params.document_ids == ["doc1"]
    assert start.draft.params.question_presented == "Draft a memo on the cap."
    assert start.draft.draft_type == "memo"


# -------------------------------------------------------------- the trace


async def test_the_trace_records_the_query_retrieval_actually_ran() -> None:
    """Not the question the attorney typed. Before `TurnTrace`, the route
    scraped stage *detail strings* for this, which could never report a
    rewritten query because no event carries one."""

    class Rewriter:
        async def arewrite(self, question, history=()):  # noqa: ANN001, ANN201, ARG002
            from app.ai.orchestration.rewriter import RewriteResult

            return RewriteResult(
                query="Does the MSA cap liability?",
                original=question,
                source="model_rewrote",
                rewritten=True,
                reasoning="resolved 'it' to the MSA",
                referents=("it",),
                usage=Usage(input_tokens=120, output_tokens=24, latency_ms=190.0),
                model="claude-haiku-4-5",
                prompt_version="rewrite/v1+beef",
            )

    engine = _engine()
    engine._ask.rewriter = Rewriter()
    request = _request("Does it cap liability?")
    await _drain(engine.answer(request))

    assert request.trace.queries == ["Does the MSA cap liability?"]
    rewrite = request.trace.detail["rewrite"]
    assert rewrite["rewritten"] is True
    assert rewrite["original"] == "Does it cap liability?"
    assert rewrite["referents"] == ["it"]
    assert any(c["stage"] == "rewrite" for c in request.trace.calls)


async def test_the_trace_records_rejected_candidates_not_only_survivors() -> None:
    """ "The clause was never retrieved" and "it scored 0.24 against a floor of
    0.25" have different fixes, and only the second is visible if the rejects
    survive. An excerpt list can only ever show what cleared the floor."""
    engine = _engine(
        retriever=_retriever(
            hits=[
                Hit(chunk(1, text=CAP_TEXT, parent="p1", doc=1), 0.9),
                Hit(chunk(2, text="unrelated recitals", parent="p2", doc=1), 0.4),
            ],
            scores={"c1": 0.88, "c2": 0.24},
            floor=0.25,
        )
    )
    request = _request()
    await _drain(engine.answer(request))

    candidates = request.trace.candidates
    assert len(candidates) == 2
    kept = [c for c in candidates if c["kept"]]
    rejected = [c for c in candidates if not c["kept"]]
    assert len(kept) == 1 and len(rejected) == 1
    assert rejected[0]["score"] == pytest.approx(0.24)


async def test_the_trace_records_what_the_citation_gate_did() -> None:
    engine = _engine(chat=FakeChat("The cap is twelve months.[[1]] And more.[[7]]"))
    request = _request()
    await _drain(engine.answer(request))

    gate = request.trace.detail["gate"]
    assert gate["citations"] == [1]
    assert gate["stripped"] == [7]
    assert gate["fabricated"] == []
    assert request.trace.detail["workflow"] == "ask"


async def test_the_trace_is_written_when_the_client_disconnects_mid_stream() -> None:
    """The turn whose trace matters most: the record is all anyone will ever see
    of it (NFR-7). Written from a `finally`, so closing the generator early
    still produces one."""
    engine = _engine()
    request = _request()

    stream = engine.answer(request)
    seen = 0
    async for _ in stream:
        seen += 1
        if seen == 5:
            break
    await stream.aclose()

    assert request.trace.detail.get("workflow") == "ask"
    assert request.trace.queries, "the query must be recorded even on a disconnect"


async def test_summary_candidates_say_which_field_retrieved_them() -> None:
    """Nine field queries over one document return overlapping passages. Nine
    unattributed rows for the same span cannot be read."""
    engine = _engine()
    request = _request()
    await _drain(engine.summarize(request))

    stages = {c["stage"] for c in request.trace.candidates}
    assert stages == {
        "rerank:parties",
        "rerank:term",
        "rerank:obligations",
        "rerank:payment",
        "rerank:termination",
        "rerank:liability",
        "rerank:assignment",
        "rerank:law",
        "rerank:unusual",
    }


async def test_the_routing_stage_reports_the_latency_it_measured() -> None:
    """A first stage claiming 0ms makes every later stage's cumulative figure
    read as if routing were free. On `auto` it is a model call on the NFR-1
    critical path, before retrieval has started."""
    engine = _engine(router=FakeRouter("ask"))
    request = _request()
    await engine.route_intent(request)
    events = await _drain(engine.answer(request))

    done = next(
        e
        for e in events
        if e.type == "stage" and e.stage.key == "routing" and e.stage.status == "done"
    )
    assert done.stage.elapsed_ms == pytest.approx(310.0)


async def test_a_summary_trace_carries_the_per_field_breakdown() -> None:
    engine = _engine()
    request = _request()
    await _drain(engine.summarize(request))

    summary = request.trace.detail["summary"]
    assert summary["fields"] == 9
    assert summary["fields_present"] == 9
    assert len(summary["per_field"]) == 9
    assert all("query" in f for f in summary["per_field"])
    assert len(request.trace.queries) == 9
    assert request.trace.detail["coverage"]["total_pages"] == 312


async def test_a_draft_trace_carries_its_sections() -> None:
    engine = _engine()
    request = _request(draft_params=DraftParams(draft_type="memo", document_ids=["doc1"]))
    await _drain(engine.draft(request))

    draft = request.trace.detail["draft"]
    assert draft["draft_type"] == "memo"
    assert draft["sections"] == ["qp", "short", "facts", "discussion", "conclusion"]
    assert len(request.trace.calls) == 5


# ------------------------------------------------------------- scenarios


@pytest.mark.parametrize(
    ("scenario", "kind"),
    [("llm_down", "llm_unavailable"), ("rate_limited", "rate_limited"), ("timeout", "timeout")],
)
async def test_a_forced_error_lands_after_retrieval_so_excerpts_survive(
    scenario: str, kind: str
) -> None:
    """NFR-6, and the reason the injection point is not the top of the stream:
    `excerptsAvailable: true` has to be true."""
    engine = _engine()
    events = await _drain(engine.answer(_request(scenario=scenario)))
    types = _types(events)

    assert types[-1] == "error"
    assert events[-1].error.kind == kind
    assert events[-1].error.excerpts_available is True
    assert "excerpt" in types, "the excerpts must really have been emitted"
    assert "token" not in types, "nothing may be generated after the failure"


async def test_no_scenario_leaves_the_stream_untouched() -> None:
    engine = _engine()
    events = await _drain(engine.answer(_request(scenario=None)))
    assert _types(events)[-1] == "done"
    assert "error" not in _types(events)


# ------------------------------------------------------- engine selection


def test_auto_falls_back_to_fixtures_when_the_ai_layer_is_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai import engine as engine_module

    monkeypatch.setenv("LL_AI_ENGINE", "auto")
    monkeypatch.setattr(engine_module, "_configured", lambda: "auto")
    monkeypatch.setattr(
        "app.ai.orchestrated_engine.unavailable_reason", lambda *a, **k: "no credentials"
    )
    engine_module.reset_engine()
    try:
        assert engine_module.get_engine().name.startswith("fixture")
    finally:
        engine_module.reset_engine()


def test_auto_resolves_to_the_real_engine_when_it_can_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai import engine as engine_module

    monkeypatch.setattr(engine_module, "_configured", lambda: "auto")
    monkeypatch.setattr("app.ai.orchestrated_engine.unavailable_reason", lambda *a, **k: None)
    engine_module.reset_engine()
    try:
        assert engine_module.get_engine().name == "orchestrated/m4"
    finally:
        engine_module.reset_engine()


def test_orchestrated_is_honoured_even_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A demo that silently answers from scripted fixtures looks exactly like
    one that works. Asking for the real engine and getting fixtures is the worst
    failure this product has, so it is never done implicitly."""
    from app.ai import engine as engine_module

    monkeypatch.setattr(engine_module, "_configured", lambda: "orchestrated")
    monkeypatch.setattr(
        "app.ai.orchestrated_engine.unavailable_reason", lambda *a, **k: "no credentials"
    )
    engine_module.reset_engine()
    try:
        assert engine_module.get_engine().name == "orchestrated/m4"
    finally:
        engine_module.reset_engine()


def test_an_unknown_engine_name_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo that silently serves fixtures for every turn is found weeks later
    by an attorney wondering why every answer cites the same MSA."""
    from app.ai import engine as engine_module

    monkeypatch.setattr(engine_module, "_configured", lambda: "langchian")
    engine_module.reset_engine()
    try:
        with pytest.raises(ValueError, match="LL_AI_ENGINE must be one of"):
            engine_module.get_engine()
    finally:
        engine_module.reset_engine()


def test_describe_says_whether_the_answers_are_real(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai import engine as engine_module

    monkeypatch.setattr(engine_module, "_configured", lambda: "fixture")
    engine_module.reset_engine()
    try:
        described = engine_module.describe()
        assert described["configured"] == "fixture"
        assert described["grounded"] is False
        assert described["resolved"].startswith("fixture")
        assert described["promptVersion"]
        assert described["indexVersion"]
    finally:
        engine_module.reset_engine()


def test_unavailable_reason_names_what_is_missing() -> None:
    """Checked rather than attempted: the alternative is discovering it on the
    first turn, after the route has already committed two message rows."""
    assert unavailable_reason(_settings(api_key="")) == (
        "no Anthropic credentials (set ANTHROPIC_API_KEY or LL_AI_API_KEY)"
    )
    assert unavailable_reason(_settings(api_key="sk-ant-x")) is None
