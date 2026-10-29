"""The whole chain, over HTTP, with the real orchestrated engine.

Everything else tests a layer. This tests the seams between them, which is what
"integrated" actually means:

    POST /api/ask
      -> session auth, rate limit, idempotency
      -> scope resolution against the real database
      -> OrchestratedEngine.route_intent  (fake router)
      -> AskWorkflow / SummaryWorkflow / DraftWorkflow  (fake retriever, fake model)
      -> SSE over the wire
      -> TurnAccumulator
      -> message, citations, trace, usage and audit rows
      -> GET /api/conversations/{id} re-renders the same turn

The retriever and the chat model are fakes; everything between the HTTP request
and the database is real. That is the point — the bugs this file exists to catch
are the ones where each layer is individually correct: a port that went async,
an event the engine forgot to emit, a trace column filled from the wrong place.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessageChunk
from sqlalchemy import select

from app.ai.orchestrated_engine import OrchestratedEngine
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
from app.db import get_sessionmaker
from tests.conftest import WORKSPACE_SCOPE, all_of, collect_stream, first_of, types_of
from tests.test_retrieval import FakeEmbedder, FakeReranker, FakeStore, chunk

CAP_TEXT = (
    "Liability of either party under this Agreement shall not exceed the fees paid in "
    "the twelve (12) months preceding the claim."
)
ANSWER = "The cap is twelve months of fees paid before the claim.[[1]]"


# ----------------------------------------------------------------- doubles


class FakeRouter:
    def __init__(self, intent: str = "ask") -> None:
        self.intent = intent
        self.calls = 0

    async def aclassify(self, request):  # noqa: ANN001, ANN201
        self.calls += 1
        return IntentDecision(
            intent=self.intent,  # type: ignore[arg-type]
            confidence=0.93,
            rationale="The request asks what a clause says.",
            source="model",
            usage=Usage(input_tokens=380, output_tokens=31, latency_ms=240.0),
            model="claude-haiku-4-5",
            prompt_version="intent/v1+test",
        )


class FakeChat:
    def __init__(self, text: str = ANSWER) -> None:
        self.text = text

    async def astream(self, messages):  # noqa: ANN001, ANN201, ARG002
        for word in self.text.split(" "):
            yield AIMessageChunk(content=word + " ")
        yield AIMessageChunk(
            content="",
            usage_metadata={"input_tokens": 9100, "output_tokens": 140, "total_tokens": 9240},
        )


class FakeStructuredChat:
    async def ainvoke(self, messages):  # noqa: ANN001, ANN201, ARG002
        class _Msg:
            usage_metadata = {"input_tokens": 2400, "output_tokens": 55}

        return {
            "parsed": FieldExtraction(
                reasoning="section 12.3 caps liability",
                present=True,
                value_md="Capped at twelve (12) months of fees.[[1]]",
                citations=[1],
            ),
            "raw": _Msg(),
            "parsing_error": None,
        }

    def with_structured_output(self, schema, **kwargs):  # noqa: ANN001, ANN201, ARG002
        return self


@pytest.fixture
def ai_engine(monkeypatch: pytest.MonkeyPatch, settings) -> OrchestratedEngine:  # noqa: ANN001
    """The real engine, with the two expensive dependencies faked.

    Patched into the route module rather than into `app.ai.engine`: `turns.py`
    does `from app.ai.engine import get_engine`, which binds the function object
    into its own namespace, so replacing the attribute on the provider module
    would have no effect on the route — a mistake worth making exactly once.
    """
    cfg = RouterSettings(api_key="sk-ant-test")  # type: ignore[call-arg]
    store = FakeStore(
        [Hit(chunk(84, text=CAP_TEXT, parent="p12", doc=1), 0.91)],
        [chunk(84, text=CAP_TEXT, parent="p12", is_parent=True, doc=1)],
    )
    retriever = Retriever(
        store=store, embedder=FakeEmbedder(), reranker=FakeReranker(), relevance_floor=0.25
    )
    engine = OrchestratedEngine(cfg)
    engine._router = FakeRouter()
    engine._retriever_cache = retriever
    engine._ask = AskWorkflow(
        retriever=retriever,
        rewriter=None,
        settings=cfg,
        chat_model=FakeChat(),
        prompt=load_named_prompt("ask_generation"),
    )
    engine._summary = SummaryWorkflow(
        retriever=retriever,
        settings=cfg,
        chat_model=FakeStructuredChat(),
        prompt=load_named_prompt("summary_extraction"),
    )
    engine._draft = DraftWorkflow(
        retriever=retriever,
        settings=cfg,
        chat_model=FakeChat("Consent may not be unreasonably withheld.[[1]]"),
        prompt=load_named_prompt("draft_generation"),
    )

    import app.api.routes.turns as turns_module

    monkeypatch.setattr(turns_module, "get_engine", lambda: engine)
    return engine


# ------------------------------------------------------------------- ask


async def test_ask_streams_a_grounded_answer_over_sse(
    corpus: AsyncClient, ai_engine: OrchestratedEngine
) -> None:
    events = await collect_stream(
        corpus, "/api/ask", {"text": "What is the liability cap?", "scope": WORKSPACE_SCOPE}
    )
    types = types_of(events)

    # The envelope the engine owns, then the workflow's own stages.
    assert types[:4] == ["stage", "stage", "intent", "scope"]
    assert first_of(events, "intent")["intent"] == "ask"
    assert types[-1] == "done"

    answer = "".join(e["text"] for e in events if e["type"] == "token")
    assert "twelve months of fees" in answer

    citation = first_of(events, "citation")["citation"]
    assert citation["n"] == 1
    assert citation["page"] == 84
    assert citation["documentName"] == "Agreement 1"
    assert citation["spanId"].startswith("doc1:84:")


async def test_the_router_is_consulted_once_per_turn(
    corpus: AsyncClient, ai_engine: OrchestratedEngine
) -> None:
    """`answer()` reads the decision off the trace; a second classification per
    turn would double the routing bill on every request."""
    conversation = (await corpus.post("/api/conversations", json={})).json()
    await collect_stream(
        corpus,
        f"/api/conversations/{conversation['id']}/messages",
        {"text": "What is the liability cap?", "mode": "auto", "scope": WORKSPACE_SCOPE},
    )
    assert ai_engine._router.calls == 1


async def test_an_explicit_mode_endpoint_skips_the_router(
    corpus: AsyncClient, ai_engine: OrchestratedEngine
) -> None:
    """The chip the attorney pressed wins, and costs no tokens."""
    await collect_stream(
        corpus, "/api/ask", {"text": "What is the liability cap?", "scope": WORKSPACE_SCOPE}
    )
    assert ai_engine._router.calls == 0


# --------------------------------------------------------------- persistence


async def test_the_turn_is_persisted_and_re_renders_identically(
    corpus: AsyncClient, ai_engine: OrchestratedEngine
) -> None:
    """The contract the thread depends on: what streamed is what is stored."""
    conversation = (await corpus.post("/api/conversations", json={})).json()
    events = await collect_stream(
        corpus,
        f"/api/conversations/{conversation['id']}/messages",
        {"text": "What is the liability cap?", "mode": "ask", "scope": WORKSPACE_SCOPE},
    )

    reread = (await corpus.get(f"/api/conversations/{conversation['id']}")).json()
    assistant = [t for t in reread["turns"] if t["role"] == "assistant"][-1]

    assert assistant["status"] == "complete"
    assert assistant["kind"] == "answer"
    assert assistant["intent"] == "ask"
    assert "twelve months of fees" in assistant["text"]
    assert assistant["citations"] == [e["citation"] for e in all_of(events, "citation")]
    assert assistant["excerpts"] == [e["excerpt"] for e in all_of(events, "excerpt")]
    assert assistant["versions"]["model"] == "claude-sonnet-5"
    assert assistant["versions"]["prompt"].startswith("ask/")


async def test_the_trace_records_the_full_candidate_set_and_the_cost_breakdown(
    corpus: AsyncClient, ai_engine: OrchestratedEngine
) -> None:
    """NFR-11 and NFR-8, read back out of the database.

    Before `TurnTrace`, `queries_json` held scraped stage *detail strings* and
    `candidates_json` was derived from the excerpt events. This asserts the
    route now persists what the engine reported.
    """
    from app.models import Trace, UsageTransaction

    await collect_stream(
        corpus, "/api/ask", {"text": "What is the liability cap?", "scope": WORKSPACE_SCOPE}
    )

    async with get_sessionmaker()() as db:
        trace = (await db.execute(select(Trace))).scalars().one()
        rows = list((await db.execute(select(UsageTransaction))).scalars())

    assert trace.queries_json == ["What is the liability cap?"]
    assert trace.candidates_json
    candidate = trace.candidates_json[0]
    assert candidate["document_id"] == "doc1"
    assert candidate["page"] == 84
    assert candidate["kept"] is True
    assert candidate["score"] > 0

    detail = trace.timings_json["detail"]
    assert detail["workflow"] == "ask"
    assert detail["gate"]["citations"] == [1]
    assert detail["gate"]["fabricated"] == []
    assert trace.timings_json["calls"], "per-call costs must be recorded"

    kinds = {r.kind for r in rows}
    assert "turn" in kinds, "the turn total stays, so historical reports do not shift"
    assert "generate" in kinds, "and the generation call is attributed separately"
    generate = next(r for r in rows if r.kind == "generate")
    assert generate.model == "claude-sonnet-5"
    assert generate.input_tokens > 0


async def test_an_audit_row_records_the_spans_the_answer_rested_on(
    corpus: AsyncClient, ai_engine: OrchestratedEngine
) -> None:
    """DR-4. The spans are the real retrieved ones, not a fixture's."""
    from app.models import AuditLog
    from app.services import audit

    await collect_stream(
        corpus, "/api/ask", {"text": "What is the liability cap?", "scope": WORKSPACE_SCOPE}
    )

    async with get_sessionmaker()() as db:
        rows = list((await db.execute(select(AuditLog))).scalars())

    generated = [r for r in rows if r.action == audit.ACTION_GENERATE]
    assert generated, "a generation must be audited"
    event = generated[-1]
    assert event.span_ids_json == ["doc1:84:0-100"]
    assert event.model_version == "claude-sonnet-5"
    assert event.prompt_version.startswith("ask/")


# ------------------------------------------------------------------ summary


async def test_summarize_streams_fields_and_persists_the_summary(
    corpus: AsyncClient, ai_engine: OrchestratedEngine
) -> None:
    documents = (await corpus.get("/api/documents")).json()
    ready = next(d for d in documents if d["status"] == "ready")

    events = await collect_stream(
        corpus,
        "/api/summarize",
        {
            "text": "Summarise this agreement",
            "scope": {"kind": "document", "documentIds": [ready["id"]]},
        },
    )
    types = types_of(events)

    assert types[:4] == ["stage", "stage", "intent", "scope"]
    assert first_of(events, "intent")["intent"] == "summary"
    start = first_of(events, "summary_start")["summary"]
    assert start["documentId"] == ready["id"]
    assert start["templateName"]
    fields = all_of(events, "summary_field")
    assert len(fields) == start["fieldCount"] if "fieldCount" in start else len(fields) > 0
    assert fields, "a summary must emit at least one field"
    assert types[-1] == "done"

    reread = (await corpus.get("/api/conversations")).json()["items"]
    conversation = reread[0]
    detail = (await corpus.get(f"/api/conversations/{conversation['id']}")).json()
    assistant = [t for t in detail["turns"] if t["role"] == "assistant"][-1]

    assert assistant["kind"] == "summary"
    assert assistant["summary"] is not None
    assert assistant["summary"]["documentId"] == ready["id"]
    assert len(assistant["summary"]["fields"]) == len(fields)
    assert assistant["summary"]["coverage"]["totalPages"] == ready["pages"]


# -------------------------------------------------------------------- draft


async def test_draft_streams_sections_and_carries_the_unreviewed_marking(
    corpus: AsyncClient, ai_engine: OrchestratedEngine
) -> None:
    from app.ai.disclaimers import DRAFT_MARKING

    documents = (await corpus.get("/api/documents")).json()
    ready = next(d for d in documents if d["status"] == "ready")

    events = await collect_stream(
        corpus,
        "/api/draft",
        {
            "text": "Draft a memo on the liability cap",
            "scope": {"kind": "document", "documentIds": [ready["id"]]},
            "draftParams": {
                "draftType": "memo",
                "questionPresented": "Whether liability is capped.",
                "audience": "supervising partner",
                "position": "",
                "tone": "neutral predictive",
                "documentIds": [ready["id"]],
            },
        },
    )

    assert first_of(events, "intent")["intent"] == "draft"
    sections = all_of(events, "draft_section")
    assert [s["section"]["key"] for s in sections] == [
        "qp",
        "short",
        "facts",
        "discussion",
        "conclusion",
    ]
    assert first_of(events, "done")["disclaimer"] == DRAFT_MARKING


async def test_a_draft_with_no_sources_is_refused_before_the_engine(
    corpus: AsyncClient, ai_engine: OrchestratedEngine
) -> None:
    """FR-19 is a route-level policy decision, not a retrieval outcome. It has
    to stay that way: an ungrounded draft is the failure this product exists to
    avoid, and a 400 naming the reason is clearer than a refusal event."""
    response = await corpus.post(
        "/api/draft",
        json={
            "text": "Draft a memo",
            "scope": WORKSPACE_SCOPE,
            "draftParams": {"draftType": "memo", "documentIds": []},
        },
    )
    assert response.status_code == 400
    assert "at least one source document" in response.json()["message"]


# ----------------------------------------------------------------- refusals


async def test_an_empty_workspace_refuses_without_calling_the_model(
    auth: AsyncClient, ai_engine: OrchestratedEngine
) -> None:
    """`auth` rather than `corpus`: a signed-in user with no documents."""
    events = await collect_stream(
        auth, "/api/ask", {"text": "What is the liability cap?", "scope": WORKSPACE_SCOPE}
    )

    assert types_of(events) == ["stage", "stage", "intent", "scope", "refusal", "done"]
    refusal = first_of(events, "refusal")["refusal"]
    assert refusal["kind"] == "out_of_scope"
    assert refusal["suggestions"]


async def test_a_refusal_is_persisted_as_refused_not_as_an_error(
    auth: AsyncClient, ai_engine: OrchestratedEngine
) -> None:
    """TS-3: a refusal is a first-class success state."""
    conversation = (await auth.post("/api/conversations", json={})).json()
    await collect_stream(
        auth,
        f"/api/conversations/{conversation['id']}/messages",
        {"text": "What is the cap?", "mode": "ask", "scope": WORKSPACE_SCOPE},
    )
    detail = (await auth.get(f"/api/conversations/{conversation['id']}")).json()
    assistant = [t for t in detail["turns"] if t["role"] == "assistant"][-1]

    assert assistant["status"] == "refused"
    assert assistant["refusal"] is not None


async def test_a_forced_outage_keeps_the_excerpts_readable(
    corpus: AsyncClient, ai_engine: OrchestratedEngine
) -> None:
    """NFR-6 end to end, through the dev scenario header the Demo menu sends."""
    events = await collect_stream(
        corpus,
        "/api/ask",
        {"text": "What is the liability cap?", "scope": WORKSPACE_SCOPE},
        headers={"X-LegalLens-Scenario": "llm_down"},
    )
    types = types_of(events)

    assert types[-1] == "error"
    error = first_of(events, "error")["error"]
    assert error["kind"] == "llm_unavailable"
    assert error["excerptsAvailable"] is True
    assert all_of(events, "excerpt"), "the excerpts must really have been emitted"
    assert "token" not in types
