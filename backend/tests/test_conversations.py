"""Conversations: the NCC endpoint, pagination, patching, and replay."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import WORKSPACE_SCOPE, collect_stream


async def test_new_chat_creates_an_empty_conversation(auth: AsyncClient) -> None:
    """The NCC endpoint. Returns the full object so the client can route to it
    without a second fetch."""
    response = await auth.post("/api/conversations", json={})
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "New chat"
    assert body["turns"] == []
    assert body["turnCount"] == 0
    assert body["pinned"] is False
    assert body["lastMessageAt"]


async def test_first_message_titles_the_thread(auth: AsyncClient, corpus: AsyncClient) -> None:
    """ "New chat" × 10 in the sidebar is a real usability problem."""
    conversation = (await corpus.post("/api/conversations", json={})).json()
    await collect_stream(
        corpus,
        f"/api/conversations/{conversation['id']}/messages",
        {
            "text": "What is the liability cap under the MSA?",
            "mode": "ask",
            "scope": WORKSPACE_SCOPE,
        },
    )
    refreshed = (await corpus.get(f"/api/conversations/{conversation['id']}")).json()
    assert refreshed["title"].startswith("What is the liability cap")
    assert refreshed["title"] != "New chat"


async def test_explicit_title_is_not_overwritten(corpus: AsyncClient) -> None:
    conversation = (
        await corpus.post("/api/conversations", json={"title": "Arclight diligence"})
    ).json()
    await collect_stream(
        corpus,
        f"/api/conversations/{conversation['id']}/messages",
        {"text": "What is the liability cap?", "mode": "ask", "scope": WORKSPACE_SCOPE},
    )
    refreshed = (await corpus.get(f"/api/conversations/{conversation['id']}")).json()
    assert refreshed["title"] == "Arclight diligence"


async def test_rename_and_pin(auth: AsyncClient) -> None:
    conversation = (await auth.post("/api/conversations", json={})).json()

    renamed = await auth.patch(
        f"/api/conversations/{conversation['id']}", json={"title": "Assignment analysis"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Assignment analysis"

    pinned = await auth.patch(f"/api/conversations/{conversation['id']}", json={"pinned": True})
    assert pinned.json()["pinned"] is True


async def test_patch_rejects_an_empty_body_and_an_empty_title(auth: AsyncClient) -> None:
    conversation = (await auth.post("/api/conversations", json={})).json()

    empty = await auth.patch(f"/api/conversations/{conversation['id']}", json={})
    assert empty.status_code == 400
    assert empty.json()["action"]

    blank = await auth.patch(f"/api/conversations/{conversation['id']}", json={"title": "   "})
    assert blank.status_code == 400


async def test_pinned_conversations_sort_first(auth: AsyncClient) -> None:
    ids = [
        (await auth.post("/api/conversations", json={"title": f"Thread {i}"})).json()["id"]
        for i in range(4)
    ]
    await auth.patch(f"/api/conversations/{ids[0]}", json={"pinned": True})

    items = (await auth.get("/api/conversations")).json()["items"]
    assert items[0]["id"] == ids[0], "a pinned thread sorts above newer unpinned ones"
    assert items[0]["pinned"] is True


async def test_cursor_pagination_is_stable_and_complete(auth: AsyncClient) -> None:
    """An offset cursor breaks when a thread is pinned or receives a message
    mid-scroll; this asserts the opaque cursor does not."""
    created = {
        (await auth.post("/api/conversations", json={"title": f"Thread {i:02d}"})).json()["id"]
        for i in range(12)
    }

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        url = "/api/conversations?limit=5" + (f"&cursor={cursor}" if cursor else "")
        page = (await auth.get(url)).json()
        seen.extend(item["id"] for item in page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            break

    assert cursor is None, "pagination did not terminate"
    assert len(seen) == len(set(seen)), "a conversation appeared on two pages"
    assert created <= set(seen), "pagination skipped a conversation"


async def test_pagination_crosses_the_pinned_boundary(auth: AsyncClient) -> None:
    """The case the keyset predicate exists for: a page boundary that falls
    inside the pinned block, and another that falls after it."""
    ids = [
        (await auth.post("/api/conversations", json={"title": f"Thread {i:02d}"})).json()["id"]
        for i in range(9)
    ]
    for pinned_id in ids[:4]:
        await auth.patch(f"/api/conversations/{pinned_id}", json={"pinned": True})

    seen: list[dict] = []
    cursor: str | None = None
    for _ in range(10):
        url = "/api/conversations?limit=3" + (f"&cursor={cursor}" if cursor else "")
        page = (await auth.get(url)).json()
        seen.extend(page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            break

    assert cursor is None
    assert len(seen) == 9, f"expected all 9 conversations, got {len(seen)}"
    assert len({c["id"] for c in seen}) == 9, "a conversation appeared twice"
    # Pinned rows must all precede unpinned ones, across page boundaries.
    flags = [c["pinned"] for c in seen]
    assert flags == sorted(flags, reverse=True), f"pinned ordering broke across pages: {flags}"
    assert sum(flags) == 4


async def test_search_filters_by_title(auth: AsyncClient) -> None:
    await auth.post("/api/conversations", json={"title": "Assignment and change of control"})
    await auth.post("/api/conversations", json={"title": "Liability cap"})

    results = (await auth.get("/api/conversations?q=assignment")).json()["items"]
    assert len(results) == 1
    assert "Assignment" in results[0]["title"]


async def test_limit_is_capped(auth: AsyncClient) -> None:
    response = await auth.get("/api/conversations?limit=5000")
    assert response.status_code == 422, "an unbounded page size is a DoS vector"


async def test_messages_endpoint_pages_turns(corpus: AsyncClient) -> None:
    conversation = (await corpus.post("/api/conversations", json={})).json()
    for text in ("What is the liability cap?", "What about termination?"):
        await collect_stream(
            corpus,
            f"/api/conversations/{conversation['id']}/messages",
            {"text": text, "mode": "ask", "scope": WORKSPACE_SCOPE},
        )

    page = (await corpus.get(f"/api/conversations/{conversation['id']}/messages?limit=2")).json()
    assert len(page["items"]) == 2
    assert page["nextCursor"]

    rest = (
        await corpus.get(
            f"/api/conversations/{conversation['id']}/messages?cursor={page['nextCursor']}"
        )
    ).json()
    assert len(rest["items"]) == 2
    assert rest["nextCursor"] is None

    roles = [t["role"] for t in page["items"] + rest["items"]]
    assert roles == ["user", "assistant", "user", "assistant"]


async def test_turn_counts_and_dominant_kind_are_maintained(corpus: AsyncClient) -> None:
    """Maintained on write — the sidebar reads them on every page."""
    conversation = (await corpus.post("/api/conversations", json={})).json()
    await collect_stream(
        corpus,
        f"/api/conversations/{conversation['id']}/messages",
        {
            "text": "Summarise the agreement",
            "mode": "summary",
            "scope": {"kind": "document", "documentIds": ["doc_msa"]},
        },
    )

    summary = (await corpus.get("/api/conversations")).json()["items"][0]
    assert summary["turnCount"] == 2
    assert summary["dominantKind"] == "summary"


async def test_delete_is_a_soft_delete_that_hides_the_thread(auth: AsyncClient) -> None:
    """Conversations are soft-deleted so DR-4's audit trail outlives them.
    Documents are hard-deleted (FR-26); the distinction is deliberate."""
    conversation = (await auth.post("/api/conversations", json={"title": "Gone"})).json()
    assert (await auth.delete(f"/api/conversations/{conversation['id']}")).status_code == 204

    assert (await auth.get(f"/api/conversations/{conversation['id']}")).status_code == 404
    titles = [c["title"] for c in (await auth.get("/api/conversations")).json()["items"]]
    assert "Gone" not in titles


async def test_deleting_a_conversation_is_audited(auth: AsyncClient) -> None:
    from sqlalchemy import select

    from app.db import get_sessionmaker
    from app.models import AuditLog

    conversation = (await auth.post("/api/conversations", json={})).json()
    await auth.delete(f"/api/conversations/{conversation['id']}")

    async with get_sessionmaker()() as db:
        actions = list((await db.execute(select(AuditLog.action))).scalars())
    assert "conversation.delete" in actions


async def test_unknown_conversation_is_404_with_an_action(auth: AsyncClient) -> None:
    response = await auth.get("/api/conversations/c_does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["message"]
    assert body["action"], "FR-28: every variant names a next action"


async def test_replayed_turn_matches_what_streamed(corpus: AsyncClient) -> None:
    """The reason `commitTurns` is deleted: the backend persists the turn
    itself, and the replay must be faithful without the client pushing it back."""
    conversation = (await corpus.post("/api/conversations", json={})).json()
    events = await collect_stream(
        corpus,
        f"/api/conversations/{conversation['id']}/messages",
        {
            "text": "What happens to the agreement on a change of control?",
            "mode": "ask",
            "scope": WORKSPACE_SCOPE,
        },
    )
    streamed_text = "".join(e["text"] for e in events if e["type"] == "token")
    streamed_citations = [e["citation"] for e in events if e["type"] == "citation"]
    streamed_excerpts = [e["excerpt"] for e in events if e["type"] == "excerpt"]
    streamed_scope = next(e for e in events if e["type"] == "scope")["scopeUsed"]
    streamed_usage = next(e for e in events if e["type"] == "usage")

    replayed = (await corpus.get(f"/api/conversations/{conversation['id']}")).json()
    turn = [t for t in replayed["turns"] if t["role"] == "assistant"][-1]

    assert turn["text"] == streamed_text
    assert turn["citations"] == streamed_citations
    assert turn["excerpts"] == streamed_excerpts
    assert turn["scopeUsed"] == streamed_scope
    assert turn["usage"] == streamed_usage["usage"]
    assert turn["versions"] == streamed_usage["versions"]
    assert turn["traceId"] == streamed_usage["traceId"]
    assert turn["disclaimer"] == next(e for e in events if e["type"] == "done")["disclaimer"]
    assert turn["stages"], "the stage trace must survive a reload"


async def test_user_turn_preserves_mode_scope_and_draft_params(corpus: AsyncClient) -> None:
    conversation = (await corpus.post("/api/conversations", json={})).json()
    params = {
        "draftType": "memo",
        "questionPresented": "May Arclight assign?",
        "audience": "supervising partner",
        "position": "",
        "tone": "cautious",
        "documentIds": ["doc_msa"],
    }
    await collect_stream(
        corpus,
        f"/api/conversations/{conversation['id']}/messages",
        {
            "text": "Draft a memo",
            "mode": "draft",
            "scope": {"kind": "subset", "documentIds": ["doc_msa"]},
            "draftParams": params,
        },
    )
    replayed = (await corpus.get(f"/api/conversations/{conversation['id']}")).json()
    user_turn = [t for t in replayed["turns"] if t["role"] == "user"][-1]

    assert user_turn["mode"] == "draft"
    assert user_turn["scope"] == {"kind": "subset", "documentIds": ["doc_msa"]}
    assert user_turn["draftParams"]["tone"] == "cautious"
    assert user_turn["draftParams"]["questionPresented"] == params["questionPresented"]
