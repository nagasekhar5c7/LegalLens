"""FR-28, NFR-6 and NFR-7: the designed failure states.

Every variant names a next action; retrieval survives an LLM outage; and no
partial output is ever recorded as complete.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import WORKSPACE_SCOPE, all_of, collect_stream, first_of

LIABILITY_Q = "What is the liability cap?"


def _scenario(name: str) -> dict[str, str]:
    return {"x-legallens-scenario": name}


async def _latest_assistant(client: AsyncClient) -> dict:
    page = (await client.get("/api/conversations")).json()
    conversation = (await client.get(f"/api/conversations/{page['items'][0]['id']}")).json()
    return [t for t in conversation["turns"] if t["role"] == "assistant"][-1]


# ------------------------------------------------------------ FR-28 variants


@pytest.mark.parametrize(
    ("scenario", "kind"),
    [
        ("llm_down", "llm_unavailable"),
        ("rate_limited", "rate_limited"),
        ("timeout", "timeout"),
    ],
)
async def test_every_error_variant_names_a_next_action(
    corpus: AsyncClient, scenario: str, kind: str
) -> None:
    events = await collect_stream(
        corpus,
        "/api/ask",
        {"text": LIABILITY_Q, "mode": "ask", "scope": WORKSPACE_SCOPE},
        headers=_scenario(scenario),
    )
    error = first_of(events, "error")
    assert error is not None, f"scenario {scenario} produced no error event"
    body = error["error"]
    assert body["kind"] == kind
    assert body["action"], "FR-28: an error with no next action is useless"
    assert body["message"]


async def test_rate_limited_error_carries_a_retry_window(corpus: AsyncClient) -> None:
    events = await collect_stream(
        corpus,
        "/api/ask",
        {"text": LIABILITY_Q, "mode": "ask", "scope": WORKSPACE_SCOPE},
        headers=_scenario("rate_limited"),
    )
    body = first_of(events, "error")["error"]
    assert body["retryAfterSec"] > 0, "a countdown needs a number"


# ------------------------------------------------------- NFR-6 degraded mode


@pytest.mark.parametrize("scenario", ["llm_down", "rate_limited", "timeout"])
async def test_retrieval_survives_a_provider_outage(corpus: AsyncClient, scenario: str) -> None:
    """NFR-6: when the LLM is down, retrieval and excerpt display keep working.

    This is the only reason the product is usable during an outage, and it is
    why errors are emitted *after* retrieval rather than instead of it.
    """
    events = await collect_stream(
        corpus,
        "/api/ask",
        {"text": LIABILITY_Q, "mode": "ask", "scope": WORKSPACE_SCOPE},
        headers=_scenario(scenario),
    )
    excerpts = all_of(events, "excerpt")
    assert excerpts, "retrieval output was lost along with generation"
    assert first_of(events, "error")["error"]["excerptsAvailable"] is True
    # And the excerpts are real text, not placeholders.
    assert excerpts[0]["excerpt"]["text"].strip()


async def test_scope_is_still_reported_during_an_outage(corpus: AsyncClient) -> None:
    events = await collect_stream(
        corpus,
        "/api/ask",
        {"text": LIABILITY_Q, "mode": "ask", "scope": WORKSPACE_SCOPE},
        headers=_scenario("llm_down"),
    )
    assert first_of(events, "scope") is not None, "FR-9 holds in degraded mode too"


# --------------------------------------------------- NFR-7 partial output


async def test_interrupted_stream_persists_as_incomplete(corpus: AsyncClient) -> None:
    """NFR-7 is about what the *record* says, not only what the UI paints."""
    events = await collect_stream(
        corpus,
        "/api/ask",
        {"text": LIABILITY_Q, "mode": "ask", "scope": WORKSPACE_SCOPE},
        headers=_scenario("interrupted"),
    )
    assert all_of(events, "token"), "the scenario should emit partial text"
    assert first_of(events, "done") is None, "an interrupted stream has no done event"

    turn = await _latest_assistant(corpus)
    assert turn["status"] == "incomplete"
    assert turn["text"], "the partial text is kept"


async def test_client_disconnect_persists_as_incomplete(corpus: AsyncClient) -> None:
    """The Stop button, and the single most important case in this file.

    A client that hangs up mid-stream is not an error the engine can see: the
    task running the generator is cancelled from outside, by Starlette, the
    moment `receive()` yields `http.disconnect`. The turn still has to be
    written — as `incomplete`, with the partial text kept — or the row says
    `streaming` for ever, describing a generation that stopped.

    This regressed once, silently: `_finalise` was awaited inside a `finally`,
    where the first await re-raises the `CancelledError` that put us there, so
    the write never happened. It is shielded now, and this is what notices if
    that shield goes away.

    Driven through ASGI directly rather than through `AsyncClient`, because
    httpx's transport drains the response instead of cancelling it — the stream
    runs to completion and the turn is recorded `complete`, which is the
    opposite of the condition under test.

    If the shield is removed this test does not go green, but it hangs rather
    than failing cleanly: the cancellation is delivered inside an open database
    session, which leaves SQLite's write lock held and every later read waiting
    on it. The two `wait_for` bounds below cover the cases that can be bounded.
    A hang here means this regressed; it is not a flaky test.
    """
    import asyncio
    import json

    from app.start import app

    conversation = (await corpus.post("/api/conversations", json={})).json()
    body = json.dumps({"text": LIABILITY_Q, "mode": "ask", "scope": WORKSPACE_SCOPE}).encode()
    cookie = "; ".join(f"{k}={v}" for k, v in corpus.cookies.items()).encode()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": f"/api/conversations/{conversation['id']}/messages",
        "raw_path": None,
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"cookie", cookie),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "state": {},
    }

    sent: list[dict] = []
    disconnect = asyncio.Event()
    body_delivered = False

    async def receive() -> dict:
        nonlocal body_delivered
        if not body_delivered:
            body_delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        # Starlette races this against the stream; returning it is what makes it
        # cancel the generator, exactly as a dropped connection does.
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    task = asyncio.create_task(app(scope, receive, send))

    # Let the stream get far enough in to have partial text on screen.
    tokens = 0
    for _ in range(400):
        await asyncio.sleep(0.01)
        tokens = sum(chunk.count(b'"type":"token"') for m in sent if (chunk := m.get("body", b"")))
        if tokens >= 5:
            break
    assert tokens >= 5, "the stream should have produced tokens before the hang-up"

    headers = dict(next(m for m in sent if m["type"] == "http.response.start")["headers"])
    turn_id = headers[b"x-legallens-turn-id"].decode()

    # Hang up.
    disconnect.set()
    try:
        await asyncio.wait_for(task, timeout=10)
    except TimeoutError:  # pragma: no cover - only reachable if the fix regresses
        task.cancel()
        pytest.fail("the request never finished after the client hung up")

    async def await_written() -> dict:
        """The write is detached from the cancelled request, so poll for it."""
        while True:
            turn = (await corpus.get(f"/api/turns/{turn_id}")).json()
            if turn["status"] != "streaming":
                return turn
            await asyncio.sleep(0.02)

    try:
        # Bounded: an unshielded `_finalise` can be cancelled holding an open
        # session, which wedges SQLite's write lock and makes this read hang
        # rather than return the wrong answer. Fail in seconds either way.
        turn = await asyncio.wait_for(await_written(), timeout=5)
    except TimeoutError:  # pragma: no cover - only reachable if the fix regresses
        pytest.fail("the turn was never written; it is still marked 'streaming'")

    assert turn["status"] == "incomplete", (
        f"a turn abandoned mid-stream was recorded as {turn['status']!r}"
    )
    assert turn["text"], "the partial text is kept"
    assert "INCOMPLETE OUTPUT" in turn["text"], "FR-24: the marking is in the text itself"


async def test_incomplete_marking_is_part_of_the_text(corpus: AsyncClient) -> None:
    """FR-24: a marking the user can copy out of is theatre. It has to be in
    the text so it survives the clipboard and the Markdown export."""
    await collect_stream(
        corpus,
        "/api/ask",
        {"text": LIABILITY_Q, "mode": "ask", "scope": WORKSPACE_SCOPE},
        headers=_scenario("interrupted"),
    )
    turn = await _latest_assistant(corpus)
    assert "INCOMPLETE OUTPUT" in turn["text"]


async def test_error_with_no_output_persists_as_error_not_incomplete(
    corpus: AsyncClient,
) -> None:
    """The distinction matters: `incomplete` means "there is partial text above",
    `error` means "there is nothing"."""
    await collect_stream(
        corpus,
        "/api/ask",
        {"text": LIABILITY_Q, "mode": "ask", "scope": WORKSPACE_SCOPE},
        headers=_scenario("llm_down"),
    )
    turn = await _latest_assistant(corpus)
    assert turn["status"] == "error"
    assert turn["text"] == ""
    assert turn["error"]["kind"] == "llm_unavailable"
    # The excerpts are still on the persisted turn, so a reload keeps them.
    assert turn["excerpts"]


async def test_interrupted_summary_keeps_the_fields_it_got(corpus: AsyncClient) -> None:
    events = await collect_stream(
        corpus,
        "/api/summarize",
        {
            "text": "Summarise",
            "mode": "summary",
            "scope": {"kind": "document", "documentIds": ["doc_msa"]},
        },
        headers=_scenario("interrupted"),
    )
    delivered = all_of(events, "summary_field")
    assert 0 < len(delivered) < 9, "the scenario should cut off mid-summary"

    turn = await _latest_assistant(corpus)
    assert turn["status"] == "incomplete"
    assert len(turn["summary"]["fields"]) == len(delivered)


# ---------------------------------------------------------- HTTP error shape


async def test_http_errors_use_the_api_error_shape(corpus: AsyncClient) -> None:
    """One frontend parser handles both HTTP bodies and `{type:'error'}` events."""
    for method, url in [
        ("GET", "/api/documents/doc_nope"),
        ("GET", "/api/conversations/c_nope"),
        ("GET", "/api/spans/s_nope"),
        ("GET", "/api/traces/tr_nope"),
    ]:
        response = await corpus.request(method, url)
        assert response.status_code == 404
        body = response.json()
        assert set(body) >= {"kind", "message", "action"}, f"{url}: {body}"
        assert body["kind"] in {
            "llm_unavailable",
            "rate_limited",
            "parse_failed",
            "timeout",
            "network",
        }


async def test_validation_errors_are_one_readable_sentence(corpus: AsyncClient) -> None:
    """FastAPI's default `detail` array is not an actionable error message."""
    response = await corpus.post("/api/ask", json={"mode": "ask", "scope": WORKSPACE_SCOPE})
    assert response.status_code == 422
    body = response.json()
    assert "detail" not in body
    assert isinstance(body["message"], str)
    assert "text" in body["message"]
    assert body["action"]


async def test_unknown_body_field_is_rejected(corpus: AsyncClient) -> None:
    """`extra='forbid'`: a client typo becomes a 422, not a silently dropped field."""
    response = await corpus.post(
        "/api/ask",
        json={
            "text": LIABILITY_Q,
            "mode": "ask",
            "scope": WORKSPACE_SCOPE,
            "modee": "typo",
        },
    )
    assert response.status_code == 422
    assert "modee" in response.json()["message"]


async def test_every_response_carries_a_request_id(corpus: AsyncClient) -> None:
    ok = await corpus.get("/api/config")
    assert ok.headers.get("x-request-id")

    failed = await corpus.get("/api/documents/doc_nope")
    assert failed.headers.get("x-request-id")
    assert failed.json()["requestId"] == failed.headers["x-request-id"]


async def test_scenario_header_is_ignored_in_production(
    corpus: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dev affordance that shipped enabled would let anyone force an outage."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "env", "production")
    assert settings.scenarios_enabled is False

    events = await collect_stream(
        corpus,
        "/api/ask",
        {"text": LIABILITY_Q, "mode": "ask", "scope": WORKSPACE_SCOPE},
        headers=_scenario("llm_down"),
    )
    assert first_of(events, "error") is None, "the scenario header was honoured"
    assert first_of(events, "done") is not None


async def test_unknown_scenario_value_is_ignored(corpus: AsyncClient) -> None:
    events = await collect_stream(
        corpus,
        "/api/ask",
        {"text": LIABILITY_Q, "mode": "ask", "scope": WORKSPACE_SCOPE},
        headers=_scenario("not-a-real-scenario"),
    )
    assert first_of(events, "error") is None
    assert first_of(events, "done") is not None
