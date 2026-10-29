"""The three workflows, their event ordering, and how each outcome persists."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import WORKSPACE_SCOPE, all_of, answer_text, collect_stream, first_of, types_of

ASSIGNMENT_Q = "What happens to the agreement on a change of control?"
ABSENT_Q = "What are the force majeure provisions for pandemic delays?"


async def _latest_assistant(client: AsyncClient) -> dict:
    page = (await client.get("/api/conversations")).json()
    conversation = (await client.get(f"/api/conversations/{page['items'][0]['id']}")).json()
    return [t for t in conversation["turns"] if t["role"] == "assistant"][-1]


# ------------------------------------------------------------------ ordering


async def test_ask_emits_the_contracted_event_order(corpus: AsyncClient) -> None:
    events = await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    order = types_of(events)

    assert order[0] == "stage", "routing stage opens the stream"
    assert order.index("intent") < order.index("scope")
    assert order.index("scope") < order.index("token")
    assert order[-2:] == ["usage", "done"], "usage always precedes done"
    # A citation may never arrive before the scope that justifies it.
    assert order.index("scope") < order.index("citation")


async def test_time_to_first_token_is_under_the_nfr1_budget(corpus: AsyncClient) -> None:
    events = await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    usage = first_of(events, "usage")["usage"]
    assert usage["timeToFirstTokenMs"] < 2000, "NFR-1: p95 TTFT ≤ 2s"


async def test_citations_resolve_to_real_spans_and_pages(corpus: AsyncClient) -> None:
    """FR-11 / FR-13: the whole trust surface. A citation must land on text."""
    events = await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    citations = [e["citation"] for e in all_of(events, "citation")]
    assert citations, "the assignment question has authored citations"

    for citation in citations:
        span = await corpus.get(f"/api/spans/{citation['spanId']}")
        assert span.status_code == 200, f"{citation['spanId']} did not resolve"
        resolved = span.json()
        assert resolved["documentId"] == citation["documentId"]
        assert resolved["page"] == citation["page"]

        page = await corpus.get(f"/api/documents/{citation['documentId']}/pages/{citation['page']}")
        assert page.status_code == 200
        text = page.json()["text"]
        assert resolved["charEnd"] <= len(text), "span runs past the end of its page"
        assert text[resolved["charStart"] : resolved["charEnd"]].strip(), "span is empty"


async def test_excerpts_carry_the_cited_text_in_context(corpus: AsyncClient) -> None:
    """FR-12: excerpts display beside the answer with no further user action."""
    events = await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    excerpts = [e["excerpt"] for e in all_of(events, "excerpt")]
    assert excerpts

    for excerpt in excerpts:
        page = await corpus.get(f"/api/documents/{excerpt['documentId']}/pages/{excerpt['page']}")
        assert excerpt["text"] in page.json()["text"], "excerpt text is not on its page"
        assert 0.0 <= excerpt["relevance"] <= 1.0


# ------------------------------------------------------------------- refusal


async def test_refusal_is_a_200_not_an_error(corpus: AsyncClient) -> None:
    """FR-14 / TS-3: a refusal is a first-class success, distinct from an error."""
    events = await collect_stream(
        corpus, "/api/ask", {"text": ABSENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    refusal = first_of(events, "refusal")
    assert refusal is not None
    assert first_of(events, "error") is None
    assert not answer_text(events), "a refusal must not also produce prose"

    body = refusal["refusal"]
    assert body["kind"] in ("no_support", "out_of_scope", "below_threshold")
    assert body["lookedFor"], "FR-14: name what was searched for"
    assert body["suggestions"], "FR-14: offer a next action"
    assert body["bestRelevance"] < body["threshold"]


async def test_refusal_names_the_scope_searched(corpus: AsyncClient) -> None:
    events = await collect_stream(
        corpus, "/api/ask", {"text": ABSENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    message = first_of(events, "refusal")["refusal"]["message"]
    scope = first_of(events, "scope")["scopeUsed"]
    assert str(scope["documentCount"]) in message
    assert str(scope["pageCount"]) in message


async def test_refusal_persists_as_refused(corpus: AsyncClient) -> None:
    await collect_stream(
        corpus, "/api/ask", {"text": ABSENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    turn = await _latest_assistant(corpus)
    assert turn["status"] == "refused"
    assert turn["refusal"] is not None


async def test_out_of_scope_question_refuses_without_retrieving(corpus: AsyncClient) -> None:
    events = await collect_stream(
        corpus,
        "/api/conversations/{id}/messages".replace(
            "{id}", (await corpus.post("/api/conversations", json={})).json()["id"]
        ),
        {"text": "What is the weather in Chicago?", "mode": "auto", "scope": WORKSPACE_SCOPE},
    )
    assert first_of(events, "intent")["intent"] == "out_of_scope"
    assert first_of(events, "refusal")["refusal"]["kind"] == "out_of_scope"


async def test_empty_scope_refuses_before_spending_a_token(corpus: AsyncClient) -> None:
    """FR-7: a scope containing only non-ready documents has nothing to search."""
    events = await collect_stream(
        corpus,
        "/api/ask",
        {
            "text": ASSIGNMENT_Q,
            "mode": "ask",
            # doc_depo is `indexing`, doc_exhibits is `failed`.
            "scope": {"kind": "subset", "documentIds": ["doc_depo", "doc_exhibits"]},
        },
    )
    refusal = first_of(events, "refusal")["refusal"]
    assert refusal["kind"] == "out_of_scope"
    assert "still processing or failed to parse" in refusal["message"]
    assert not all_of(events, "token")


# -------------------------------------------------------------------- scope


async def test_non_ready_documents_are_excluded_with_a_reason(corpus: AsyncClient) -> None:
    """FR-7 + frontend nuance #7: a silent exclusion is an unexplainable answer."""
    events = await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    scope = first_of(events, "scope")["scopeUsed"]
    excluded = {e["documentId"]: e["reason"] for e in scope["excluded"]}

    assert "doc_depo" in excluded and "indexing" in excluded["doc_depo"].lower()
    assert "doc_exhibits" in excluded and "failed" in excluded["doc_exhibits"].lower()
    assert "doc_depo" not in scope["documentIds"]


async def test_document_scope_restricts_citations_to_that_document(
    corpus: AsyncClient,
) -> None:
    """FR-8: a question can be scoped to one document."""
    events = await collect_stream(
        corpus,
        "/api/ask",
        {
            "text": "What does the opinion hold about arbitration?",
            "mode": "ask",
            "scope": {"kind": "document", "documentIds": ["doc_vance"]},
        },
    )
    scope = first_of(events, "scope")["scopeUsed"]
    assert scope["kind"] == "document"
    assert scope["documentIds"] == ["doc_vance"]

    for event in all_of(events, "citation"):
        assert event["citation"]["documentId"] == "doc_vance"


# ------------------------------------------------------------------ summary


async def test_summary_streams_a_header_then_its_fields(corpus: AsyncClient) -> None:
    """FR-15: the template is driven by the document's type (BRD §9)."""
    events = await collect_stream(
        corpus,
        "/api/summarize",
        {
            "text": "Summarise this agreement",
            "mode": "summary",
            "scope": {"kind": "document", "documentIds": ["doc_msa"]},
        },
    )
    order = types_of(events)
    assert order.index("summary_start") < order.index("summary_field")

    header = first_of(events, "summary_start")
    assert header["summary"]["documentType"] == "contract"
    assert header["summary"]["templateName"] == "Contract / agreement template"

    fields = all_of(events, "summary_field")
    assert header["fieldCount"] == len(fields)
    assert [f["index"] for f in fields] == list(range(len(fields)))


async def test_summary_runs_with_no_focus_instructions(corpus: AsyncClient) -> None:
    """The composer offers focus instructions as optional, so an empty `text`
    has to reach the workflow rather than being rejected as a malformed body.

    Summary is template-driven and never reads the question
    (`SummaryWorkflow.run`), so there is nothing an empty box could degrade —
    but a `min_length=1` on `SendMessageRequest.text` made this a 422 raised
    before the route body ran, and Summary is the one mode whose Send button is
    live with an empty textarea.
    """
    events = await collect_stream(
        corpus,
        "/api/summarize",
        {
            "text": "",
            "mode": "summary",
            "scope": {"kind": "document", "documentIds": ["doc_msa"]},
        },
    )
    order = types_of(events)
    assert "summary_start" in order, "an empty question must still produce a summary"
    assert all_of(events, "summary_field"), "the template drives the fields, not the question"
    assert order[-1] == "done"


async def test_empty_question_is_rejected_outside_summary(corpus: AsyncClient) -> None:
    """The flip side: an empty question is meaningless in every other mode, and
    is refused with a next action rather than retrieved on."""
    for mode, path in (("ask", "/api/ask"), ("auto", "/api/conversations/{id}/messages")):
        if "{id}" in path:
            conversation = (await corpus.post("/api/conversations", json={})).json()
            path = f"/api/conversations/{conversation['id']}/messages"
        response = await corpus.post(
            path, json={"text": "   ", "mode": mode, "scope": WORKSPACE_SCOPE}
        )
        assert response.status_code == 400, f"{mode}: {response.text}"
        body = response.json()
        assert "question is required" in body["message"].lower()
        assert body["action"], "FR-28: every error names a next action"


async def test_a_missing_text_field_is_still_a_validation_error(corpus: AsyncClient) -> None:
    """Empty is allowed; *absent* is a client that forgot a field, and stays a
    422 naming it. The two must not collapse into one another."""
    response = await corpus.post("/api/ask", json={"mode": "ask", "scope": WORKSPACE_SCOPE})
    assert response.status_code == 422
    assert "text" in response.json()["message"]


async def test_summary_template_differs_by_document_type(corpus: AsyncClient) -> None:
    """BRD §9: a contract summary and an opinion summary share no structure."""
    labels: dict[str, list[str]] = {}
    for document_id in ("doc_msa", "doc_vance", "doc_tessara"):
        events = await collect_stream(
            corpus,
            "/api/summarize",
            {
                "text": "Summarise",
                "mode": "summary",
                "scope": {"kind": "document", "documentIds": [document_id]},
            },
        )
        labels[document_id] = [e["field"]["label"] for e in all_of(events, "summary_field")]

    assert "Holding" in labels["doc_vance"]
    assert "Holding" not in labels["doc_msa"]
    assert "Relief sought" in labels["doc_tessara"]
    assert not set(labels["doc_msa"]) & set(labels["doc_vance"])


async def test_long_document_summary_reports_coverage(corpus: AsyncClient) -> None:
    """FR-17: composed without silent truncation, stating what was covered."""
    events = await collect_stream(
        corpus,
        "/api/summarize",
        {
            "text": "Summarise",
            "mode": "summary",
            "scope": {"kind": "document", "documentIds": ["doc_msa"]},
        },
    )
    coverage = first_of(events, "coverage")["coverage"]
    assert coverage["pagesCovered"] == coverage["totalPages"] == 312
    assert coverage["passes"] > 1
    assert coverage["note"]


async def test_unauthored_document_yields_absent_fields_not_invention(
    corpus: AsyncClient,
) -> None:
    """FR-16: a field with no supporting span is empty, never filled by inference."""
    events = await collect_stream(
        corpus,
        "/api/summarize",
        {
            "text": "Summarise",
            "mode": "summary",
            "scope": {"kind": "document", "documentIds": ["doc_nda"]},
        },
    )
    fields = [e["field"] for e in all_of(events, "summary_field")]
    assert fields, "a structurally valid summary is still produced"
    assert all(f["valueMd"] is None for f in fields), "absent must mean absent"
    assert all(f["citations"] == [] for f in fields)


async def test_summary_persists_with_its_payload(corpus: AsyncClient) -> None:
    await collect_stream(
        corpus,
        "/api/summarize",
        {
            "text": "Summarise",
            "mode": "summary",
            "scope": {"kind": "document", "documentIds": ["doc_msa"]},
        },
    )
    turn = await _latest_assistant(corpus)
    assert turn["kind"] == "summary"
    assert turn["status"] == "complete"
    assert turn["summary"]["templateName"] == "Contract / agreement template"
    assert len(turn["summary"]["fields"]) == 9
    assert turn["coverage"]["totalPages"] == 312


# -------------------------------------------------------------------- draft

DRAFT_PARAMS = {
    "draftType": "memo",
    "questionPresented": "May Arclight assign without consent?",
    "audience": "supervising partner",
    "position": "assignment is permitted without consent",
    "tone": "neutral predictive",
    "documentIds": ["doc_msa", "doc_delmar"],
}


async def test_draft_without_sources_is_refused(corpus: AsyncClient) -> None:
    """FR-19: a draft with no sources cannot be grounded, so it is not produced."""
    response = await corpus.post(
        "/api/draft",
        json={
            "text": "Draft a memo",
            "mode": "draft",
            "scope": WORKSPACE_SCOPE,
            "draftParams": {**DRAFT_PARAMS, "documentIds": []},
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert "source document" in body["message"]
    assert body["action"], "FR-28: every error names a next action"


async def test_draft_streams_a_shell_then_its_sections(corpus: AsyncClient) -> None:
    """FR-18: memo in its conventional structure."""
    events = await collect_stream(
        corpus,
        "/api/draft",
        {
            "text": "Draft a memo on assignment",
            "mode": "draft",
            "scope": {"kind": "subset", "documentIds": ["doc_msa", "doc_delmar"]},
            "draftParams": DRAFT_PARAMS,
        },
    )
    order = types_of(events)
    assert order.index("draft_start") < order.index("draft_section")

    shell = first_of(events, "draft_start")["draft"]
    assert shell["draftType"] == "memo"
    assert shell["title"]
    assert shell["params"]["questionPresented"] == DRAFT_PARAMS["questionPresented"]

    headings = [e["section"]["heading"] for e in all_of(events, "draft_section")]
    assert headings == [
        "Question Presented",
        "Short Answer",
        "Statement of Facts",
        "Discussion",
        "Conclusion",
    ]


async def test_case_brief_uses_its_own_structure(corpus: AsyncClient) -> None:
    events = await collect_stream(
        corpus,
        "/api/draft",
        {
            "text": "Brief this case",
            "mode": "draft",
            "scope": {"kind": "document", "documentIds": ["doc_vance"]},
            "draftParams": {
                **DRAFT_PARAMS,
                "draftType": "case_brief",
                "documentIds": ["doc_vance"],
            },
        },
    )
    headings = [e["section"]["heading"] for e in all_of(events, "draft_section")]
    assert headings == [
        "Procedural Posture",
        "Facts",
        "Issue",
        "Holding",
        "Reasoning",
        "Disposition",
    ]


async def test_draft_carries_the_unreviewed_marking(corpus: AsyncClient) -> None:
    """FR-23 / TS-5: marked as unreviewed AI output, in a form that survives export."""
    events = await collect_stream(
        corpus,
        "/api/draft",
        {
            "text": "Draft a memo",
            "mode": "draft",
            "scope": {"kind": "subset", "documentIds": ["doc_msa", "doc_delmar"]},
            "draftParams": DRAFT_PARAMS,
        },
    )
    disclaimer = first_of(events, "done")["disclaimer"]
    assert "UNREVIEWED AI DRAFT" in disclaimer
    assert "Not legal advice" in disclaimer


async def test_draft_cites_only_the_supplied_sources(corpus: AsyncClient) -> None:
    """FR-22: no cite outside the source set — the Mata v. Avianca rule."""
    allowed = {"doc_msa", "doc_delmar"}
    events = await collect_stream(
        corpus,
        "/api/draft",
        {
            "text": "Draft a memo",
            "mode": "draft",
            "scope": {"kind": "subset", "documentIds": sorted(allowed)},
            "draftParams": DRAFT_PARAMS,
        },
    )
    cited = {e["citation"]["documentId"] for e in all_of(events, "citation")}
    assert cited, "a grounded draft cites something"
    assert cited <= allowed, f"cited outside the source set: {cited - allowed}"


# --------------------------------------------------------------- the router


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What is the liability cap?", "ask"),
        ("Summarise the master services agreement", "summary"),
        ("Draft a memo on the assignment question", "draft"),
        ("What is the weather today?", "out_of_scope"),
    ],
)
async def test_auto_mode_routes_and_says_so(corpus: AsyncClient, text: str, expected: str) -> None:
    """Frontend nuance #4: auto-routing is never silent."""
    conversation = (await corpus.post("/api/conversations", json={})).json()
    events = await collect_stream(
        corpus,
        f"/api/conversations/{conversation['id']}/messages",
        {"text": text, "mode": "auto", "scope": WORKSPACE_SCOPE},
    )
    intent = first_of(events, "intent")
    assert intent["intent"] == expected
    assert intent["mode"] == "auto", "the requested mode is echoed so `auto → ask` is visible"


async def test_explicit_mode_overrides_the_router(corpus: AsyncClient) -> None:
    """A question whose words say "summarise" still answers as an ask when the
    user pressed Ask."""
    events = await collect_stream(
        corpus,
        "/api/ask",
        {"text": "Summarise the assignment clause", "mode": "ask", "scope": WORKSPACE_SCOPE},
    )
    assert first_of(events, "intent")["intent"] == "ask"
    assert not all_of(events, "summary_field")


async def test_requested_mode_is_preserved_on_the_persisted_turn(
    corpus: AsyncClient,
) -> None:
    conversation = (await corpus.post("/api/conversations", json={})).json()
    await collect_stream(
        corpus,
        f"/api/conversations/{conversation['id']}/messages",
        {"text": "Summarise the agreement", "mode": "auto", "scope": WORKSPACE_SCOPE},
    )
    replayed = (await corpus.get(f"/api/conversations/{conversation['id']}")).json()
    turn = [t for t in replayed["turns"] if t["role"] == "assistant"][-1]
    assert turn["requestedMode"] == "auto"
    assert turn["intent"] == "summary"
