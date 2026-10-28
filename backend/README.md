# Legal Lens — Backend

FastAPI backend for Legal Lens: grounded reading and drafting over an attorney's
own documents. Every output traces to a retrieved span, or the system declines.

This is **M4**. It runs standalone end to end — real auth, real uploads, real
text extraction, real persistence, real SSE streaming, real deletion — and the
AI layer behind the three-function port is now live: intent routing, query
rewriting, hybrid retrieval, rerank, grounded generation and the citation gate.

The M3 fixture engine remains behind the same port.
[`app/ai/engine.py`](app/ai/engine.py) selects between them from `LL_AI_ENGINE`
(`auto` | `orchestrated` | `fixture`) and `GET /readyz` reports which resolved.
The swap really was one file — no route, schema or frontend change, which is the
property the port existed to buy.

---

## Run it

These commands work the same on macOS, Linux and Windows (PowerShell or
Command Prompt). You need [uv](https://docs.astral.sh/uv/) installed.

```
cd backend
uv sync --extra dev --extra ai
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.start:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/docs and sign in with `dana@example.com` /
`counsel-demo-2026`.

With the frontend too: `docker compose up --build` from the repo root, then open
http://localhost:5173.

### Everything else

```
uv run pytest
uv run ruff check app tests scripts
uv run mypy app
uv run python scripts/export_openapi.py
```

---

## Reading the code

Start with [`app/schemas/`](app/schemas/) — those are the wire types, and they
mirror `frontend/src/api/types.ts`, which is the contract. Then
[`app/api/routes/turns.py`](app/api/routes/turns.py) for the streaming path, and
[`app/ai/ports.py`](app/ai/ports.py) for the boundary the AI layer sits behind.

```
app/
├─ main.py            app factory, middleware order, lifespan
├─ config.py          every setting, LL_-prefixed
├─ db.py              async engine, WAL pragmas
├─ sse.py             SSE framing and keep-alives
├─ schemas/           the wire contract (camelCase; see below)
├─ models/            16 SQLAlchemy tables
├─ api/
│  ├─ deps.py         session auth, DB session, scenario, idempotency
│  ├─ errors.py       ApiError — FR-28
│  └─ routes/         auth config conversations turns documents blobs platform health
├─ middleware/        request-id + timing, rate limiting
├─ services/          auth conversations documents scope turns usage audit
├─ storage/           AES-256-GCM blob store, signed read URLs
├─ ingestion/         queue, worker, pipeline, validation, parsers, stubs
└─ ai/
   ├─ ports.py        AiEngine, AiRequest, TurnTrace — the whole boundary
   ├─ engine.py       the one place an engine is chosen
   ├─ orchestrated_engine.py   the M4 adapter
   ├─ fixture_engine.py        the M3 implementation
   ├─ fixtures/       the scripted corpus the fixture engine serves
   └─ orchestration/
      ├─ router.py       intent classification (Haiku 4.5)
      ├─ rewriter.py     pre-retrieval query rewriting (Haiku 4.5)
      ├─ citations.py    the FR-21/FR-22 gate and the sentence gate
      ├─ context.py      retrieved text, fenced as data not instructions
      ├─ templates.py    the summary schemas and draft section plans
      ├─ prompts/        one *.prompt.md per call, version-stamped by content hash
      ├─ retrieval/      bge-m3, Qdrant, the cross-encoder, the write path
      └─ workflows/      ask · summary · draft, plus shared generation helpers
```

### The wire is camelCase

`answerMd`, `scopeUsed`, `draftParams`, `timeToFirstTokenMs`. Pydantic does this
with an alias generator; snake_case is still accepted on **input**, so curl and
the tests stay readable. `tests/test_contract.py` parses the frontend's
`types.ts` directly and fails if the two drift — including every string-literal
union and all fifteen `StreamEvent` variants.

### Nulls on the wire are load-bearing

`SummaryField.valueMd` is `string | null`, and the null is how FR-16
distinguishes *"not addressed in this document"* from a field that was never
sent. SSE frames therefore do **not** use `exclude_none`. Do not add it back.

### SSE

`POST` endpoints return `text/event-stream`:

```
event: token
data: {"type":"token","text":"The assignment clause"}
```

The discriminator is in the JSON as well as on the `event:` line. Because these
are POSTs, a client cannot use `EventSource` — the frontend uses `fetch` plus a
`ReadableStream` line parser. A `: keep-alive` comment goes out every 15s so a
three-minute summary (NFR-3) survives idle-connection reapers.

Event order is contractual:
`stage → intent → scope → [refusal | workflow events] → coverage? → usage → done`.

### Lifecycle trap worth knowing

A `StreamingResponse` generator runs **after** the route function returns, so
the request-scoped DB session is already closed by then. Everything the stream
persists opens its own session inside the generator
([`app/api/routes/turns.py`](app/api/routes/turns.py), `_finalise`). Using the
request's session passes in tests and fails under load.

Relatedly: async SQLAlchemy raises `MissingGreenlet` on a lazy load rather than
quietly issuing a query, so every serialiser that needs a relationship takes it
as an argument (`to_schema(document, jobs=...)`) instead of reading it off the
ORM object.

---

## What is real in M3, and what is a stub

| | Status |
|---|---|
| Auth, sessions, middleware chain, rate limiting | **real** |
| Upload, magic-byte validation, bomb guards, SHA-256 dedupe | **real** |
| AES-256-GCM at rest for files *and* extracted page text | **real** |
| Text extraction — TXT, DOCX (incl. tables), PDF text layer | **real** |
| Page-local span offsets, `chunk_meta`, section-aware chunking | **real** |
| Ingestion queue, status machine, retry, crash recovery | **real** |
| Conversations, cursor pagination, turn persistence and replay | **real** |
| SSE streaming, refusals, incomplete-stream handling | **real** |
| Audit log, trace, usage ledger, feedback | **real** |
| Hard delete, verified by test | **real** |
| OCR for scanned PDFs — Tesseract or RapidOCR over a pypdfium2 rasteriser | **real**, behind an extra |
| Embeddings (bge-m3) and vector upsert into Qdrant, on upload | **real**, behind an extra |
| Encrypted page images for scanned pages | **real**, behind an extra |
| Intent routing (Haiku 4.5, structured output) | **real**, behind an extra |
| Query rewriting for follow-ups (Haiku 4.5) | **real**, behind an extra |
| Hybrid retrieval, RRF fusion, cross-encoder rerank, parent expansion | **real**, behind an extra |
| Ask / Summary / Draft generation (Sonnet 5) | **real**, behind an extra |
| Citation resolution and the FR-22 authority gate | **real**, behind an extra |
| Groundedness check | **real**, reported not enforced — M5 calibrates it |
| Layout-aware parsing (Docling / unstructured) | stub |

"Behind an extra" means the code is real and tested but its dependencies are
optional, because `uv sync` with no extras must still produce a backend that
starts. What a given install will actually do is reported by
`scripts/try_ingest.py --check` and on `GET /readyz`, and recorded per page in
`document_pages.parse_engine` — a silent fallback is how a demo works on one
machine and indexes nothing on another.

    uv sync --extra dev --extra ai --extra retrieval --extra ocr
    uv run python scripts/try_ingest.py --check

OCR also needs the Tesseract program installed separately (pip cannot provide it).

Each stub sits behind a port in [`app/ingestion/ports.py`](app/ingestion/ports.py)
or [`app/ai/ports.py`](app/ai/ports.py). The stubs never invent content: a PDF
with no readable text layer is **quarantined with a reason**, not indexed with
fabricated text. That is deliberate — an answerable-but-empty document is worse
than a visible failure (BRD §15).

### The two ingestion paths

The branch is decided by measurement, not by file extension: `has_no_text_layer`
compares mean characters per page against `LL_TEXT_LAYER_CHARS_PER_PAGE_FLOOR`,
because a scanned fax yields a handful of stray characters per page rather than
zero and a `== 0` check would index the garbage.

    PDF with a text layer ─┐
    DOCX                   ├─→ chunk → embed → Qdrant → ready
    TXT                    │
    scanned PDF → OCR ─────┘

The gate is per document; the work inside it is per page. Once a file is on the
OCR branch only its below-floor pages are recognised, so a part-scanned PDF
keeps the text-layer extraction for the pages that had one. A mostly
born-digital document with a few scanned exhibits has a mean above the floor and
so never enters the branch — a known gap, argued in the `pipeline.py` docstring,
whose fix is the same layout analysis BRD §15 defers.

Everything after the merge is shared: chunking, embedding and indexing never
learn which branch produced the text, and the only trace of the difference is
the per-page `ocr_confidence` that the DR-1 gate reads.

### Where indexing is wired

`app/indexing.py` is the only module in the repository that imports both
`app.ingestion` and `app.ai`, and it exists because `tests/test_layering.py`
forbids the crossing in both directions: ingestion must not import `app.ai`
(parsing should not depend on how answers get written) and the AI layer must not
import `app.models` or `sqlalchemy` (an AI layer that could read the database
could retrieve a document the scope resolver excluded). It is the same single
wiring point that `app/ai/engine.py` is for the engine.

### How the AI layer is wired

```
app/ai/
├─ ports.py              AiEngine, AiRequest, TurnTrace — the whole boundary
├─ engine.py             the one place an engine is chosen (LL_AI_ENGINE)
├─ orchestrated_engine.py  ← the M4 adapter: AiRequest ⇄ orchestration
├─ fixture_engine.py     the M3 implementation, still selectable
└─ orchestration/        router · rewriter · retrieval · citation gate · workflows
```

`orchestrated_engine.py` is the only module that imports both the port and the
orchestration layer, and it has three jobs: adapt `AiRequest` into each
workflow's arguments and each workflow's outcome back into `TurnTrace`; emit the
turn's envelope events (`stage(routing)` → `intent` → `scope`), which belong to
the turn rather than to any one workflow; and decide the three things that
precede a workflow — whether anything is in scope at all, whether the router
said this is not something Legal Lens does, and which document a summary is of.

The workflows themselves never import the port. That is enforced
(`test_layering.py::test_the_orchestration_layer_does_not_import_the_port`) and
it is what keeps them drivable from `scripts/try_ask.py` and testable with
literals.

The three topologies differ, and each module's docstring argues why:

| | retrieval | generation | FR-22 on a fabricated cite |
|---|---|---|---|
| **Ask** | once | streamed, sentence-gated | halts the stream → refusal |
| **Summary** | once *per template field* | per field | withholds that field, keeps the rest |
| **Draft** | once | per section, each seeing the last | withholds the **whole** draft |

Summary's per-field retrieval is what makes a 312-page agreement summarisable:
one top-10 over "summarise this agreement" answers the nine fields from whatever
the document talks about most. Draft's all-or-nothing gate is the opposite call
from Summary's for a reason — a summary is nine independent findings and losing
one leaves eight useful ones, whereas a memo is one argument, and a Short Answer
whose Discussion was withheld asserts a conclusion whose support has been
removed while still reading as finished work.

### What M5 still replaces

1. `app/ingestion/stub/layout_qa.py` and `classifier.py` — both still heuristic
   proxies, and both say so.
2. `LL_AI_RELEVANCE_FLOOR` and `LL_AI_GROUNDEDNESS_FLOOR` — defensible defaults
   carried over from the M3 fixture's scale, not yet calibrated against a
   labelled set. The groundedness check reports rather than enforces until it is.
3. The eval harness: the prompts carry labelled example tables that the tests
   read, but nothing scores a run against a gold set yet.

`tests/test_layering.py` enforces every boundary above by scanning imports, so
none of it can erode quietly.

---

## Endpoints

33 operations across 26 paths. `openapi.json` is committed; regenerate it with
`uv run python scripts/export_openapi.py`.

| | |
|---|---|
| `POST /api/auth/login` · `/logout` · `GET /api/auth/me` | FR-27 |
| `GET /api/config` | FR-2 limits, so the client holds no second copy |
| `POST /api/conversations` | the NCC endpoint |
| `GET /api/conversations?cursor=&limit=&q=` | keyset-paged |
| `GET` · `PATCH` · `DELETE /api/conversations/{id}` | `PATCH` takes title and pinned |
| `GET /api/conversations/{id}/messages` | paged turns |
| `POST /api/conversations/{id}/messages` | **SSE** — router decides |
| `POST /api/ask` · `/api/summarize` · `/api/draft` | **SSE** — explicit mode |
| `GET /api/turns/{id}` | re-read one turn after a lost stream |
| `POST /api/documents` | multipart, multi-file, per-file outcomes |
| `GET /api/documents?type=&status=&sort=` | FR-6 |
| `GET /api/documents/{id}` · `/status` | `/status` is the poll target |
| `GET /api/documents/{id}/pages/{n}` · `/image` | ETag'd; image via signed URL |
| `PATCH /api/documents/{id}` · `POST /retry` · `DELETE` | type override, retry, hard delete |
| `GET /api/blobs/{token}` | 60s signed read, bound to the session |
| `GET /api/spans/{id}` | FR-11 |
| `POST /api/feedback` · `GET /api/traces/{id}` · `GET /api/usage` | FR-25, NFR-11, NFR-8 |
| `GET /api/workspace/health` · `DELETE /api/workspace` | needs `{"confirm":"DELETE"}` |
| `GET /healthz` · `/readyz` | liveness, readiness |

---

## Configuration

Full list with comments in [`.env.example`](.env.example). The two that matter:

- **`LL_BLOB_KEY`** — AES-256-GCM over uploads, extracted page text and page
  images. **Losing it means losing every stored document.** Required in
  production; outside production a stable key is written to
  `data/.dev-secrets.json` on first run and reused. (A per-process random key
  would make blobs from the previous run undecryptable, which is a real bug this
  codebase had for about an hour.)
- **`LL_SECRET_KEY`** — signs session cookies and blob read URLs.

Both are refused-at-startup requirements when `LL_ENV=production`.

`LL_STREAM_DELAY_FACTOR` scales the fixture engine's artificial delays. It is
`1.0` by default, which reproduces the ~600ms TTFT the streaming UI was built
against; the test suite sets `0.02`, which is why 155 tests run in 36 seconds.

### Dev affordance: scenario forcing

`X-LegalLens-Scenario: refusal | llm_down | rate_limited | timeout | interrupted | slow`
forces that path, so the frontend's Demo menu keeps working against the real
backend and FR-28 / NFR-6 stay reachable from the UI. Ignored outright when
`LL_ENV=production`.

---

## Testing

```
uv run pytest
uv run pytest tests/test_hard_delete.py -v
```

| | |
|---|---|
| `test_contract.py` | parses `types.ts`; camelCase, unions, event variants |
| `test_auth.py` | sessions, revocation, enumeration resistance |
| `test_conversations.py` | pagination across the pinned boundary, replay fidelity |
| `test_documents.py` | real PDF/DOCX/TXT ingestion, FR-2 wording, page-local offsets |
| `test_turns.py` | all three workflows, event order, routing, FR-16/17/19/22 |
| `test_errors.py` | every `ApiErrorKind`, degraded mode, incomplete persistence |
| `test_audit.py` | DR-4 completeness, trace, usage budgets, DR-5 log filter |
| `test_hard_delete.py` | **FR-26 / TS-6** — see below |
| `test_layering.py` | NFR-14, by import scan |
| `test_migrations.py` | migrations match the models; single head; reversible |

`tests/test_hard_delete.py` is not a check on the delete feature — per TS-6 it
*is* the requirement ("verified by test, not asserted"). Deleting those tests
means FR-26 is no longer satisfied regardless of what the code does.

The suite creates its schema with `create_all` for speed;
`test_migrations.py` runs Alembic for real and diffs the result, so that
shortcut cannot hide schema drift.

---

## Integrating the frontend

The frontend currently resolves everything from `src/mocks/`. To point it here:

1. Rewrite the bodies in `src/api/client.ts` as `fetch` calls. **Signatures do
   not change**, so no component changes.
2. Add `src/api/sse.ts` — `fetch` + `ReadableStream` + a line parser. It is in
   the frontend's folder plan but was never written, because `EventSource`
   cannot POST.
3. **Delete `commitTurns`.** It is a mock-only artefact; the backend persists
   each turn as it streams. This is the one breaking change.
4. Delete `src/mocks/` — its fixtures now live in `app/ai/fixtures/`, so the
   corpus, spans and scripted answers are identical.
5. Point the Demo menu at the `X-LegalLens-Scenario` header.
6. Send `credentials: 'include'` on every request; the session is a cookie.

`types.ts` needs no changes at all — which was the point of writing it first.
