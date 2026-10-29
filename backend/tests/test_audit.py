"""DR-4, NFR-8, NFR-11, NFR-12 and DR-5 — the accountability surfaces.

DR-4 requires an audit row for every retrieval and generation, carrying document
IDs, retrieved spans, prompt, model version, output and timestamp. These tests
check it is written *with* the turn: an audit write that can fail independently
of the thing it records is not an audit trail.
"""

from __future__ import annotations

import logging

from httpx import AsyncClient
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import AuditLog, Feedback, UsageTransaction
from tests.conftest import WORKSPACE_SCOPE, collect_stream, first_of

ASSIGNMENT_Q = "What happens to the agreement on a change of control?"
ABSENT_Q = "What are the force majeure provisions for pandemic delays?"


async def _audit_rows(action: str | None = None) -> list[AuditLog]:
    async with get_sessionmaker()() as db:
        stmt = select(AuditLog).order_by(AuditLog.created_at)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        return list((await db.execute(stmt)).scalars())


# -------------------------------------------------------------------- DR-4


async def test_generation_writes_a_complete_audit_row(corpus: AsyncClient) -> None:
    events = await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    usage = first_of(events, "usage")

    rows = await _audit_rows("ai.generate")
    assert len(rows) == 1
    row = rows[0]

    assert row.user_id
    assert row.conversation_id
    assert row.message_id
    assert row.document_ids_json, "DR-4: the documents searched"
    assert row.span_ids_json, "DR-4: the spans retrieved"
    assert row.prompt_version == usage["versions"]["prompt"]
    assert row.model_version == usage["versions"]["model"]
    assert row.index_version == usage["versions"]["index"]
    assert row.trace_id == usage["traceId"]
    assert row.output_sha256, "DR-4: the output, recorded as a hash"
    assert row.created_at


async def test_audit_stores_a_hash_not_the_output(corpus: AsyncClient) -> None:
    """DR-5: the audit log quotes client content, so it holds as little as it
    can — the open question in BRD §16 is about retaining this very table."""
    events = await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    answer = "".join(e["text"] for e in events if e["type"] == "token")
    row = (await _audit_rows("ai.generate"))[0]

    assert len(row.output_sha256) == 64
    assert answer[:40] not in str(row.output_sha256)
    assert answer[:40] not in str(row.detail_json)


async def test_refusal_is_audited_under_its_own_action(corpus: AsyncClient) -> None:
    """A correct refusal is a success at ≥90% target; it needs to be countable
    separately from a generation."""
    await collect_stream(
        corpus, "/api/ask", {"text": ABSENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    rows = await _audit_rows("ai.refuse")
    assert len(rows) == 1
    assert rows[0].detail_json["status"] == "refused"
    assert await _audit_rows("ai.generate") == []


async def test_audit_records_the_scope_and_what_was_excluded(corpus: AsyncClient) -> None:
    await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    detail = (await _audit_rows("ai.generate"))[0].detail_json
    assert detail["scope_kind"] == "workspace"
    assert detail["excluded"] == 2, "doc_depo and doc_exhibits are not ready"
    assert detail["citations"] > 0
    assert detail["engine"]


async def test_audit_commits_with_the_turn(corpus: AsyncClient) -> None:
    """One transaction: either the turn and its audit row both exist, or neither."""
    await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    async with get_sessionmaker()() as db:
        from app.models import Message

        assistant = (
            (await db.execute(select(Message).where(Message.role == "assistant"))).scalars().one()
        )
        audit = (
            (await db.execute(select(AuditLog).where(AuditLog.message_id == assistant.id)))
            .scalars()
            .one()
        )
    assert audit.trace_id == assistant.trace_id


async def test_upload_and_rejection_are_both_audited(auth: AsyncClient) -> None:
    from tests.conftest import FIXTURES, upload

    await upload(auth, FIXTURES / "nda.pdf", FIXTURES / "slides.pptx")
    actions = [row.action for row in await _audit_rows()]
    assert "doc.upload" in actions
    assert "doc.upload_rejected" in actions


async def test_type_override_is_audited_with_both_values(corpus: AsyncClient) -> None:
    await corpus.patch("/api/documents/doc_depo", json={"type": "pleading"})
    row = (await _audit_rows("doc.type_override"))[0]
    assert row.detail_json == {"from": "other", "to": "pleading"}


async def test_draft_policy_refusal_is_audited(corpus: AsyncClient) -> None:
    """FR-19's refusal is a policy decision and has to be visible as one."""
    await corpus.post(
        "/api/draft",
        json={
            "text": "Draft a memo",
            "mode": "draft",
            "scope": WORKSPACE_SCOPE,
            "draftParams": {"draftType": "memo", "documentIds": []},
        },
    )
    rows = await _audit_rows("policy.refuse")
    assert len(rows) == 1
    assert "no source documents" in rows[0].detail_json["reason"]


# ------------------------------------------------------------ NFR-11 / NFR-12


async def test_trace_is_written_and_readable(corpus: AsyncClient) -> None:
    events = await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    trace_id = first_of(events, "usage")["traceId"]

    response = await corpus.get(f"/api/traces/{trace_id}")
    assert response.status_code == 200
    trace = response.json()

    assert trace["traceId"] == trace_id
    assert trace["messageId"]
    assert trace["stages"], "NFR-11: per-stage timings"
    assert all("elapsedMs" in s for s in trace["stages"])
    assert trace["candidates"], "NFR-11: the candidate set with scores"
    assert all(0.0 <= c["score"] <= 1.0 for c in trace["candidates"])
    assert trace["versions"]["model"], "NFR-12: reproducible"
    assert trace["timings"]["latency_ms"] is not None


async def test_trace_records_the_queries_a_refusal_ran(corpus: AsyncClient) -> None:
    """A refusal whose queries are not recorded cannot be diagnosed."""
    events = await collect_stream(
        corpus, "/api/ask", {"text": ABSENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    trace_id = first_of(events, "usage")["traceId"]
    trace = (await corpus.get(f"/api/traces/{trace_id}")).json()
    assert trace["queries"], "no record of what was looked for"


async def test_another_users_trace_is_not_readable(corpus: AsyncClient, app) -> None:
    from httpx import ASGITransport
    from httpx import AsyncClient as Client

    from app.models import User
    from app.services import auth as auth_service

    events = await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    trace_id = first_of(events, "usage")["traceId"]

    async with get_sessionmaker()() as db:
        db.add(
            User(
                email="marcus@example.com",
                password_hash=auth_service.hash_password("paralegal-pass-1"),
                workspace_name="Marcus",
            )
        )
        await db.commit()

    async with Client(transport=ASGITransport(app=app), base_url="http://test") as other:
        await other.post(
            "/api/auth/login",
            json={"email": "marcus@example.com", "password": "paralegal-pass-1"},
        )
        assert (await other.get(f"/api/traces/{trace_id}")).status_code == 404


# --------------------------------------------------------------------- NFR-8


async def test_usage_row_is_written_per_turn(corpus: AsyncClient) -> None:
    events = await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    usage = first_of(events, "usage")["usage"]

    async with get_sessionmaker()() as db:
        rows = list(
            (
                await db.execute(select(UsageTransaction).where(UsageTransaction.kind == "turn"))
            ).scalars()
        )
    assert len(rows) == 1
    assert rows[0].usd == usage["usd"]
    assert rows[0].input_tokens == usage["inputTokens"]
    assert rows[0].ttft_ms == usage["timeToFirstTokenMs"]


async def test_usage_ledger_totals_and_budget(corpus: AsyncClient) -> None:
    for text in (ASSIGNMENT_Q, "What is the payment term?"):
        await collect_stream(
            corpus, "/api/ask", {"text": text, "mode": "ask", "scope": WORKSPACE_SCOPE}
        )

    ledger = (await corpus.get("/api/usage")).json()
    assert len(ledger["rows"]) == 2
    assert ledger["totalAsks"] == 2
    assert ledger["totalUsd"] == round(sum(r["usd"] for r in ledger["rows"]), 6)
    assert ledger["budgetPerAskUsd"] == 0.05
    assert ledger["meanUsdPerAsk"] <= 0.05, "NFR-8: ≤ $0.05 per Ask"
    assert all(r["title"] for r in ledger["rows"])


async def test_cost_per_ask_is_within_budget(corpus: AsyncClient) -> None:
    events = await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    assert first_of(events, "usage")["usage"]["usd"] <= 0.05


# ------------------------------------------------------------------- FR-25


async def test_feedback_joins_the_turn_and_its_trace(corpus: AsyncClient) -> None:
    events = await collect_stream(
        corpus, "/api/ask", {"text": ABSENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    trace_id = first_of(events, "usage")["traceId"]
    page = (await corpus.get("/api/conversations")).json()
    conversation = (await corpus.get(f"/api/conversations/{page['items'][0]['id']}")).json()
    turn_id = [t for t in conversation["turns"] if t["role"] == "assistant"][-1]["id"]

    response = await corpus.post(
        "/api/feedback",
        json={
            "turnId": turn_id,
            "verdict": "down",
            # The signal that matters most: a refusal the user says was wrong.
            "reasons": ["wrong_refusal"],
            "note": "The clause is in Schedule 6.",
        },
    )
    assert response.status_code == 204

    async with get_sessionmaker()() as db:
        row = (await db.execute(select(Feedback))).scalars().one()
    assert row.message_id == turn_id
    assert row.trace_id == trace_id, "FR-25: linked to the retrieval trace"
    assert row.reasons_json == ["wrong_refusal"]


async def test_feedback_is_idempotent_per_turn(corpus: AsyncClient) -> None:
    await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    page = (await corpus.get("/api/conversations")).json()
    conversation = (await corpus.get(f"/api/conversations/{page['items'][0]['id']}")).json()
    turn_id = [t for t in conversation["turns"] if t["role"] == "assistant"][-1]["id"]

    for verdict in ("down", "up"):
        await corpus.post(
            "/api/feedback", json={"turnId": turn_id, "verdict": verdict, "reasons": []}
        )

    async with get_sessionmaker()() as db:
        rows = list((await db.execute(select(Feedback))).scalars())
    assert len(rows) == 1, "a second submission should update, not duplicate"
    assert rows[0].verdict == "up"


async def test_feedback_on_a_user_turn_is_rejected(corpus: AsyncClient) -> None:
    await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    page = (await corpus.get("/api/conversations")).json()
    conversation = (await corpus.get(f"/api/conversations/{page['items'][0]['id']}")).json()
    user_turn = [t for t in conversation["turns"] if t["role"] == "user"][0]

    response = await corpus.post(
        "/api/feedback", json={"turnId": user_turn["id"], "verdict": "up", "reasons": []}
    )
    assert response.status_code == 400
    assert response.json()["action"]


async def test_feedback_appears_on_the_replayed_turn(corpus: AsyncClient) -> None:
    await collect_stream(
        corpus, "/api/ask", {"text": ASSIGNMENT_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}
    )
    page = (await corpus.get("/api/conversations")).json()
    conversation_id = page["items"][0]["id"]
    conversation = (await corpus.get(f"/api/conversations/{conversation_id}")).json()
    turn_id = [t for t in conversation["turns"] if t["role"] == "assistant"][-1]["id"]

    await corpus.post(
        "/api/feedback",
        json={"turnId": turn_id, "verdict": "up", "reasons": [], "note": "Useful."},
    )
    replayed = (await corpus.get(f"/api/conversations/{conversation_id}")).json()
    turn = [t for t in replayed["turns"] if t["role"] == "assistant"][-1]
    assert turn["feedback"]["verdict"] == "up"
    assert turn["feedback"]["note"] == "Useful."


# --------------------------------------------------------------------- DR-5


async def test_log_filter_suppresses_document_content(caplog: object, corpus: AsyncClient) -> None:
    """DR-5: no document content reaches telemetry or an error tracker."""
    from app.logging_setup import ContentFilter

    content_filter = ContentFilter()
    record = logging.LogRecord(
        name="legallens.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="page_text=%s",
        args=("EXCEPT FOR THE EXCLUDED CLAIMS, EACH PARTY'S AGGREGATE LIABILITY…",),
        exc_info=None,
    )
    assert content_filter.filter(record) is True
    assert "EXCLUDED CLAIMS" not in record.getMessage()
    assert "redacted" in record.getMessage()


async def test_long_log_lines_are_suppressed() -> None:
    from app.logging_setup import MAX_MESSAGE_CHARS, ContentFilter

    record = logging.LogRecord(
        name="legallens.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="x" * (MAX_MESSAGE_CHARS + 1),
        args=(),
        exc_info=None,
    )
    ContentFilter().filter(record)
    assert "redacted" in record.getMessage()


async def test_access_log_does_not_record_query_strings(corpus: AsyncClient) -> None:
    """A question can travel in a query string; the access log must not keep it."""
    import inspect

    from app.middleware import context

    source = inspect.getsource(context.RequestContextMiddleware.dispatch)
    assert "request.url.path" in source
    assert "request.url.query" not in source
    assert "str(request.url)" not in source
