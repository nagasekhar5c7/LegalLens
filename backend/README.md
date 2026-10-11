# Legal Lens — Backend

FastAPI backend for Legal Lens: grounded reading and drafting over an attorney's
own documents. Every output traces to a retrieved span, or the system declines.

This is **M3**. It runs standalone end to end — real auth, real uploads, real
text extraction, real persistence, real SSE streaming, real deletion — with the
AI layer behind a three-function port whose only M3 implementation is a
fixture engine. **M4 swaps one line** in [`app/ai/engine.py`](app/ai/engine.py).

---

## Run it

```bash
cd backend
make install        # uv sync --extra dev
make seed           # migrate + create the dev user and fixture corpus
make dev            # http://127.0.0.1:8000  (docs at /docs)
```

`make seed` prints the dev credentials (`dana@example.com` /
`counsel-demo-2026`) and two curl commands. The whole thing:

```bash
# sign in
curl -c /tmp/ll -X POST localhost:8000/api/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"dana@example.com","password":"counsel-demo-2026"}'

# stream a cited answer
curl -b /tmp/ll -N -X POST localhost:8000/api/ask \
  -H 'content-type: application/json' \
  -d '{"text":"What happens on a change of control?","mode":"ask",
       "scope":{"kind":"workspace","documentIds":[]}}'
```

With the frontend too: `docker compose up --build` from the repo root, then open
`localhost:5173`.

### Everything else

```bash
make check       # lint + mypy --strict + openapi freshness + 155 tests
make test        # pytest
make reset       # destroy the database and blobs, re-seed
make openapi     # regenerate openapi.json
make help        # the full list
```

---

## Reading the code

Start with [`app/schemas/`](app/schemas/) — those are the wire types, and they
mirror `frontend/src/api/types.ts`, which is the contract. Then
[`app/api/routes/turns.py`](app/api/routes/turns.py) for the streaming path, and
[`app/ai/ports.py`](app/ai/ports.py) for the boundary M4 lands behind.

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
└─ ai/                ports, engine provider, fixture engine, fixtures
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
| Layout-aware parsing (Docling / unstructured) | stub |
| Citation verification gate, groundedness check | stub → M4 |
| Intent router, query rewriting | keyword heuristics → M4 |

"Behind an extra" means the code is real and tested but its dependencies are
optional, because `uv sync` with no extras must still produce a backend that
starts. What a given install will actually do is reported by
`make ingest-check` and on `GET /readyz`, and recorded per page in
`document_pages.parse_engine` — a silent fallback is how a demo works on one
machine and indexes nothing on another.

    make install-all            # dev + ai + retrieval + ocr  (~2.5 GB of wheels)
    brew install tesseract      # the OCR binary; pip cannot provide it
    make ingest-check           # which engines resolved, and why not the others
    make ingest f=nda.pdf q="what is the notice period?"

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

### What M4 still replaces

1. `app/ai/engine.py` — return the orchestrated engine instead of `FixtureEngine()`.
2. `app/ingestion/stub/layout_qa.py` and `classifier.py` — both still heuristic
   proxies, and both say so.
3. The citation verification gate and the groundedness check.

No route or schema changes. `tests/test_layering.py` enforces the boundary by
scanning imports, so it cannot erode quietly.

---

## Endpoints

33 operations across 26 paths. `openapi.json` is committed and `make check`
fails if it is stale.

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

```bash
make test                       # 155 tests, ~36s
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
