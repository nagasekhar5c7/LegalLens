"""The contract tests: the wire must match `frontend/src/api/types.ts`.

These are the tests that would catch the failure mode BACKEND-TODO §0 is about —
the backend quietly growing a shape the frontend does not speak. They parse the
TypeScript file directly rather than restating its contents here, so the two
cannot drift without a failure.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from httpx import AsyncClient

from tests.conftest import WORKSPACE_SCOPE, collect_stream

TYPES_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "types.ts"

#: Endpoints the architecture and `client.ts` between them require.
REQUIRED_ROUTES = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/config"),
    ("POST", "/api/conversations"),
    ("GET", "/api/conversations"),
    ("GET", "/api/conversations/{conversation_id}"),
    ("GET", "/api/conversations/{conversation_id}/messages"),
    ("PATCH", "/api/conversations/{conversation_id}"),
    ("DELETE", "/api/conversations/{conversation_id}"),
    ("POST", "/api/conversations/{conversation_id}/messages"),
    ("POST", "/api/ask"),
    ("POST", "/api/summarize"),
    ("POST", "/api/draft"),
    ("POST", "/api/documents"),
    ("GET", "/api/documents"),
    ("GET", "/api/documents/{document_id}"),
    ("GET", "/api/documents/{document_id}/status"),
    ("GET", "/api/documents/{document_id}/pages/{page_no}"),
    ("PATCH", "/api/documents/{document_id}"),
    ("POST", "/api/documents/{document_id}/retry"),
    ("DELETE", "/api/documents/{document_id}"),
    ("GET", "/api/spans/{span_id}"),
    ("POST", "/api/feedback"),
    ("GET", "/api/traces/{trace_id}"),
    ("GET", "/api/usage"),
    ("GET", "/api/workspace/health"),
    ("DELETE", "/api/workspace"),
}


def _ts_union(name: str) -> set[str]:
    """Pull a string-literal union out of types.ts, e.g. `export type Mode = ...`."""
    source = TYPES_TS.read_text()
    match = re.search(rf"export type {name}\s*=\s*([^\n]*(?:\n\s*\|[^\n]*)*)", source)
    assert match, f"{name} not found in types.ts"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_openapi_declares_every_required_route(app) -> None:
    spec = app.openapi()
    declared = {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        for method in operations
    }
    missing = REQUIRED_ROUTES - declared
    assert not missing, f"endpoints missing from the API: {sorted(missing)}"


def test_openapi_is_3_1(app) -> None:
    assert app.openapi()["openapi"].startswith("3.1")


@pytest.mark.skipif(not TYPES_TS.exists(), reason="frontend types.ts not present")
def test_stream_event_variants_match_types_ts() -> None:
    """`StreamEvent` is a discriminated union; both sides must know all of it."""
    from app.schemas.events import EVENT_TYPES

    source = TYPES_TS.read_text()
    block = source.split("export type StreamEvent")[1].split("/* ---")[0]
    ts_variants = set(re.findall(r"type:\s*'([a-z_]+)'", block))

    assert ts_variants == set(EVENT_TYPES), (
        f"only in types.ts: {sorted(ts_variants - set(EVENT_TYPES))}; "
        f"only in the backend: {sorted(set(EVENT_TYPES) - ts_variants)}"
    )


@pytest.mark.skipif(not TYPES_TS.exists(), reason="frontend types.ts not present")
@pytest.mark.parametrize(
    ("ts_name", "module", "attr"),
    [
        ("Mode", "app.schemas.common", "Mode"),
        ("Intent", "app.schemas.common", "Intent"),
        ("DocType", "app.schemas.common", "DocType"),
        ("DocStatus", "app.schemas.common", "DocStatus"),
        ("ScopeKind", "app.schemas.common", "ScopeKind"),
        ("TurnStatus", "app.schemas.common", "TurnStatus"),
        ("DraftType", "app.schemas.common", "DraftType"),
        ("ApiErrorKind", "app.schemas.common", "ApiErrorKind"),
        ("FeedbackReason", "app.schemas.common", "FeedbackReason"),
        ("StageKey", "app.schemas.common", "StageKey"),
    ],
)
def test_literal_unions_match_types_ts(ts_name: str, module: str, attr: str) -> None:
    import importlib
    from typing import get_args

    backend = set(get_args(getattr(importlib.import_module(module), attr)))
    assert _ts_union(ts_name) == backend, f"{ts_name} differs from the TypeScript union"


async def test_every_response_is_camel_case(corpus: AsyncClient) -> None:
    """A snake_case key on the wire means the frontend reads `undefined`."""
    snake = re.compile(r"^[a-z]+(_[a-z0-9]+)+$")

    def walk(node: object, path: str = "") -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                if snake.match(key):
                    found.append(f"{path}.{key}")
                found.extend(walk(value, f"{path}.{key}"))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                found.extend(walk(item, f"{path}[{i}]"))
        return found

    for url in (
        "/api/config",
        "/api/auth/me",
        "/api/documents",
        "/api/conversations",
        "/api/usage",
        "/api/workspace/health",
        "/api/documents/doc_msa",
        "/api/documents/doc_msa/status",
        "/api/documents/doc_msa/pages/84",
        "/api/spans/s_msa_1223",
    ):
        response = await corpus.get(url)
        assert response.status_code == 200, f"{url}: {response.text}"
        offenders = walk(response.json())
        assert not offenders, f"{url} returned snake_case keys: {offenders}"


async def test_stream_events_are_camel_case(corpus: AsyncClient) -> None:
    events = await collect_stream(
        corpus,
        "/api/ask",
        {"text": "What is the liability cap?", "mode": "ask", "scope": WORKSPACE_SCOPE},
    )
    flat = str(events)
    for snake_key in ("'scope_used'", "'span_id'", "'answer_md'", "'elapsed_ms'", "'value_md'"):
        assert snake_key not in flat, f"{snake_key} leaked onto the wire"
    assert "'scopeUsed'" in flat
    assert "'spanId'" in flat


async def test_snake_case_is_accepted_on_input(corpus: AsyncClient) -> None:
    """curl and the tests stay readable; the frontend still sends camelCase."""
    events = await collect_stream(
        corpus,
        "/api/ask",
        {
            "text": "What is the liability cap?",
            "mode": "ask",
            "scope": {"kind": "subset", "document_ids": ["doc_msa"]},
        },
    )
    scope = next(e for e in events if e["type"] == "scope")["scopeUsed"]
    assert scope["documentIds"] == ["doc_msa"]
