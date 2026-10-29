# Legal Lens — Backend Implementation Plan

**Scope of this document:** Phase 2 (M3) only — the FastAPI backend. **No orchestration layer, no AI layer.**
The backend is built **standalone and end-to-end**: it runs, persists, streams and deletes for real, with the AI
layer behind a three-function port (`answer` · `summarize` · `draft`) whose only implementation in this phase is a
server-side fixture engine. When M4 lands, one module is swapped and no route, schema or frontend file changes.
**Stack:** Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 async + SQLite · Alembic · uv.
**Traceability:** every task carries the `FR-*` / `NFR-*` / `DR-*` / `TS-*` code it serves, auditable against
`docs/LegalLens-BRD-PRD-FRD.md` and `docs/architecture/legallens-technical-flow.excalidraw` (bands 2, 6, 7).

---

## 0. The contract already exists — this is the single most important fact in this plan

`frontend/src/api/types.ts` is not a sketch. It is 392 lines of wire types written as the contract, with
`client.ts` carrying the endpoint list in its header comment. **The backend's job is to satisfy that file, not to
invent a parallel shape.** Three consequences that drive every decision below:

1. **The wire is camelCase.** `answerMd`, `scopeUsed`, `draftParams`, `lowConfidencePages`, `timeToFirstTokenMs`.
   Pydantic models therefore use `alias_generator=to_camel` + `populate_by_name=True`, and every response is
   serialised `by_alias=True`. The backend accepts both casings on input and emits camelCase only. The
   alternative — snake_case on the wire — costs a mapping layer in the frontend for no benefit.
2. **`StreamEvent` is a discriminated union on `type`**, with thirteen variants:
   `stage · intent · scope · token · citation · excerpt · summary_field · draft_section · refusal · usage ·
   coverage · done · error`. The SSE framing must therefore carry the discriminator *inside* the JSON, and the
   `event:` line mirrors it so both an `EventSource`-style and a plain-JSON consumer work.
3. **`ApiError` (FR-28) is a type, not a status code.** `{kind, message, action, retryAfterSec?, documentId?,
   excerptsAvailable?}`. It appears both as an HTTP error body and as a `{type:'error'}` stream event, so a global
   exception handler emits exactly that object and one frontend parser handles both.

### The ten things that make this backend *legal* software, not CRUD

| # | Nuance | Why it matters | Where |
|---|---|---|---|
| 1 | **The AI layer is a port with one fixture implementation.** `AiEngine` Protocol: `answer()`, `summarize()`, `draft()`, each an async generator of `StreamEvent`. The frontend's `mocks/` move server-side behind it. | NFR-14 — the backend holds *no* retrieval or prompt logic. Enforced by test: no `app/api/**` module may import `app/ai/fixtures/**`. This is what makes M4 a swap instead of a rewrite. | `app/ai/ports.py` |
| 2 | **Refusal is a 200, not a 4xx.** A structured `Refusal` in the envelope, and the turn persists with `status='refused'`. | FR-14, TS-3. A correct refusal is a success at ≥90% target. Returning 422 would make the frontend's calm slate card unreachable. | `app/schemas/envelope.py` |
| 3 | **An interrupted stream is persisted as incomplete.** On client disconnect or generator error, the partial turn is written with `status='incomplete'` and whatever tokens were emitted — never as `complete`. | NFR-7. The requirement is about what the *record* says, not only the UI. | `app/api/routes/turns.py` |
| 4 | **Hard delete is verified by test, not asserted.** `DELETE /api/documents/{id}` removes blob, encrypted page text, span map, page images, `chunk_meta` rows and (later) vector points, inside one transaction, leaving an audit tombstone only. A test asserts every table and the blob directory. | FR-26, TS-6. "Deletion is real — verified by test, not asserted" is the literal requirement. | `tests/test_hard_delete.py` |
| 5 | **Encryption at rest is real AES-256-GCM.** Original file, extracted page text and page images are all encrypted with a per-object nonce and a key from the environment; reads go through short-lived signed URLs. | NFR-9, DR-5. Extracted text is the part people forget — it is as privileged as the PDF. | `app/storage/blob.py` |
| 6 | **Documents are untrusted input, server-side.** Magic-byte sniff (never trust `Content-Type` or extension), decompression-bomb and page-count guards, stripped PDF JS/embedded files, and parsed text is never interpolated into anything executable. | TS-4, FR-2. The hostile-PDF risk of BRD §15 lands here first, before any prompt exists. | `app/ingestion/validate.py` |
| 7 | **Ingestion is asynchronous, DB-backed, per-file.** One `ingestion_jobs` row per file; status machine `queued → parsing → ocr → indexing → ready \| failed`; one failure never fails the batch; retry with backoff; progress read by polling `/status`. | FR-3. The batch semantics are a requirement, so the queue is a row in SQLite, not a list in memory that a restart loses. | `app/ingestion/queue.py` |
| 8 | **Every retrieval and generation writes an audit row.** Document IDs, retrieved span IDs, prompt/model/index version, output hash, timestamp, user, trace ID — written in the same transaction as the message. | DR-4. If the audit write can fail independently of the turn, the audit is fiction. | `app/services/audit.py` |
| 9 | **Cost is a ledger row, not a log line.** `usage_transactions` per turn and per ingestion, queryable by `GET /api/usage`, attributable against the `$0.05/ask` and `$0.50/doc` budgets. | NFR-8, NFR-11. The frontend already has a `UsageLedger` screen expecting rows. | `app/services/usage.py` |
| 10 | **The scenario header keeps the frontend's Demo menu alive.** `X-LegalLens-Scenario: refusal \| llm_down \| rate_limited \| parse_failed \| timeout \| interrupted \| slow \| low_ocr` forces the fixture engine down that path. Dev-only, rejected when `ENV=production`. | The frontend validated P7 (every failure mode) through that menu. Losing it at integration would make FR-28 and NFR-6 untestable from the UI. | `app/ai/scenarios.py` |

---

## 1. Endpoint inventory — reconciled

The architecture names 23 endpoints. `client.ts` calls eight things the architecture never named (`setPinned`,
`deleteWorkspace`, `retryIngest`, `validateFile` limits, workspace health, logout, `commitTurns`). **Reconciled
list below is the real surface.** Marked ⊕ = not in the diagram, added because the frontend needs it.

| Method | Path | Returns | Code |
|---|---|---|---|
| `POST` | `/api/auth/login` | `{ok:true}` + `Set-Cookie` session | FR-27 |
| `POST` | `/api/auth/logout` ⊕ | `204` | FR-27 |
| `GET` | `/api/auth/me` | `{email, workspaceName}` | FR-27 |
| `GET` | `/api/config` ⊕ | `{maxBytes, maxPages, acceptedExtensions, maxDocuments, scenariosEnabled}` | FR-2 |
| `POST` | `/api/conversations` | `Conversation` — **the NCC endpoint** | US-10 |
| `GET` | `/api/conversations?cursor=&limit=&q=` | `{items: ConversationSummary[], nextCursor}` | US-10 |
| `GET` | `/api/conversations/{id}` | `Conversation` with full `turns[]` | US-10 |
| `GET` | `/api/conversations/{id}/messages?cursor=` | `{items: Turn[], nextCursor}` | US-10 |
| `PATCH` | `/api/conversations/{id}` | `{title?, pinned?}` ⊕ `pinned` | US-10 |
| `DELETE` | `/api/conversations/{id}` | `204` | US-10 |
| `POST` | `/api/conversations/{id}/messages` | **SSE** `text/event-stream` | FR-10 |
| `POST` | `/api/ask` · `/api/summarize` · `/api/draft` | **SSE**, skips the router | BRD §7 |
| `POST` | `/api/documents` | multipart, multi-file → `{accepted[], rejected[]}` | FR-1, FR-2 |
| `GET` | `/api/documents?type=&status=&sort=` | `LegalDocument[]` | FR-6 |
| `GET` | `/api/documents/{id}` | `LegalDocument` + `ingestion[]` | FR-6 |
| `GET` | `/api/documents/{id}/status` | `{status, progress, ingestion[]}` — poll target | FR-3 |
| `GET` | `/api/documents/{id}/pages/{n}` | `Page {documentId, page, text, ocrConfidence}` | FR-4, FR-13 |
| `GET` | `/api/documents/{id}/pages/{n}/image` ⊕ | signed short-lived redirect | FR-13 |
| `PATCH` | `/api/documents/{id}` | `{type}` → sets `typeSource='overridden'` | FR-5 |
| `POST` | `/api/documents/{id}/retry` ⊕ | re-enqueues the job | FR-3 |
| `DELETE` | `/api/documents/{id}` | `204` — hard delete | FR-26 |
| `GET` | `/api/spans/{spanId}` | `Span {documentId, page, charStart, charEnd, section?}` | FR-11 |
| `POST` | `/api/feedback` | `{turnId, verdict, reasons[], note?}` | FR-25 |
| `GET` | `/api/traces/{traceId}` | stages, queries, candidate IDs, scores, versions | NFR-11 |
| `GET` | `/api/usage` | `UsageLedgerRow[]` + totals | NFR-8 |
| `GET` | `/api/workspace/health` ⊕ | `WorkspaceHealth {documents, ready, indexing, failed, pages}` | US-2 |
| `DELETE` | `/api/workspace` ⊕ | `204` — type-to-confirm body | FR-26 |
| `GET` | `/healthz` · `/readyz` ⊕ | liveness / readiness | NFR-6 |

**`commitTurns` is deleted, not implemented.** It is a mock-only artefact: the real backend persists each turn
*as it streams*, so reloading a conversation shows what happened without the client pushing state back. This is
one of the few places `client.ts` must change, and it should be noted on the frontend side at integration.

---

## 2. SSE framing — the exact bytes

```
event: token
data: {"type":"token","text":"The assignment clause"}

event: citation
data: {"type":"citation","citation":{"n":1,"spanId":"s_412",...}}

: keep-alive
```

- `Content-Type: text/event-stream` · `Cache-Control: no-cache, no-transform` · `Connection: keep-alive` ·
  `X-Accel-Buffering: no` (nginx) · `retry: 3000` once at stream open.
- A `: keep-alive` comment every 15 s, so a 3-minute summary (NFR-3) survives idle-connection reapers.
- **POST + SSE means the frontend cannot use `EventSource`** (it is GET-only). It uses `fetch` +
  `ReadableStream` + a line parser — the `api/sse.ts` already listed in the frontend skeleton but not yet written.
  The backend is unaffected; naming it here so integration does not discover it.
- Event order is contractual and matches `mocks/stream.ts`:
  `stage(routing) → intent → scope → [refusal | …workflow events…] → coverage? → usage → done`.
  `usage` always precedes `done`; `error` may replace any tail.
- Ordering guarantee: a `citation` event never arrives before the `scope` event that justifies it.

---

## 3. Data model — SQLite, "conversation DB" (band 6)

Async SQLAlchemy 2.0 with `aiosqlite`, `WAL` mode, `foreign_keys=ON`, Alembic from commit one. Postgres swap at
M6 is a URL change: no SQLite-specific DDL, no `AUTOINCREMENT`, UUID7 string PKs, `TIMESTAMP` in UTC, JSON via
`sqlalchemy.JSON`.

```
users              id, email, password_hash(argon2id), workspace_name, created_at
sessions           id, user_id, expires_at, created_at, user_agent, revoked_at        FR-27
conversations      id, user_id, title, pinned, dominant_kind, turn_count,
                   last_message_at, created_at, deleted_at
messages           id, conversation_id, seq, role, text, mode, requested_mode,
                   intent, kind, status, scope_json, draft_params_json,
                   envelope_json, trace_id, created_at                      ← status: streaming|complete|
                                                                              refused|error|incomplete
message_citations  id, message_id, n, span_id, document_id, page, section, label      FR-11
documents          id, user_id, name, ext, mime, sha256, size_bytes, pages, type,
                   type_source, classifier_confidence, status, progress,
                   failure_reason, failure_detail, ocr_json, blob_key,
                   uploaded_at, deleted_at                                            FR-5, FR-6
document_pages     id, document_id, page_no, char_start, char_end,
                   ocr_confidence, text_key, image_key                                FR-4, DR-1
spans              id, document_id, page_no, char_start, char_end, section            FR-11
chunk_meta         id, document_id, parent_chunk_id, page_from, page_to,
                   char_start, char_end, section_path, clause_no, token_count,
                   index_version, vector_point_id                             ← vector_point_id NULL in M3
ingestion_jobs     id, document_id, status, step, attempt, steps_json,
                   last_error, started_at, finished_at                                FR-3
traces             id, message_id, stages_json, queries_json,
                   candidates_json, timings_json, created_at                          NFR-11
usage_transactions id, conversation_id, message_id, kind, input_tokens,
                   output_tokens, usd, latency_ms, ttft_ms, model, created_at         NFR-8
feedback           id, message_id, verdict, reasons_json, note, created_at            FR-25
audit_log          id, user_id, action, conversation_id, message_id,
                   document_ids_json, span_ids_json, prompt_version,
                   model_version, index_version, trace_id, output_sha256,
                   ip, created_at                                                     DR-4
prompt_versions    id, name, version, sha256, created_at
index_versions     id, name, embedding_model, chunker, created_at                     NFR-12
```

**Why `envelope_json` alongside normalised tables.** Citations, usage and audit are queried and gated on, so they
are columns. Stages, refusal, `summaryPayload` and `draftPayload` are only ever read back whole to re-render a
turn, so they live in one JSON column. Normalising a `DraftSection` buys nothing and costs a migration every time
a template changes. Citations are in *both* — denormalised into the envelope for replay, normalised for the
FR-21/NFR-15 citation-precision query.

---

## 4. Folder skeleton

```
backend/
├─ pyproject.toml · uv.lock · .env.example · README.md · Makefile · Dockerfile
├─ alembic.ini · migrations/versions/
├─ app/
│  ├─ main.py            app factory · lifespan (migrate, start worker) · OpenAPI 3.1 metadata
│  ├─ config.py          pydantic-settings · Settings(env_prefix="LL_")
│  ├─ db.py              async engine · session dep · WAL pragma
│  ├─ sse.py             event framing · keep-alive · disconnect detection
│  ├─ models/            users conversations messages documents pages spans chunks
│  │                     jobs traces usage feedback audit versions
│  ├─ schemas/           base(CamelModel) · auth · documents · conversations · turns
│  │                     envelope · events · feedback · usage · workspace · errors
│  ├─ api/
│  │  ├─ deps.py         current_user · db session · scenario · idempotency
│  │  ├─ errors.py       ApiError mapping · global handlers
│  │  └─ routes/         auth conversations turns documents pages spans
│  │                     feedback usage traces workspace config health
│  ├─ middleware/        request_id · session_auth · rate_limit · audit · timing
│  ├─ services/          auth conversations turns documents spans usage audit workspace
│  ├─ storage/           blob.py (AES-256-GCM) · keys.py · signed_url.py · layout.py
│  ├─ ingestion/
│  │  ├─ queue.py        DB-backed queue · claim/heartbeat/retry
│  │  ├─ worker.py       asyncio worker pool · bounded concurrency (NFR-5)
│  │  ├─ pipeline.py     the 6 steps, each a port call
│  │  ├─ validate.py     magic bytes · size · pages · bomb guards      TS-4, FR-2
│  │  ├─ ports.py        Parser · Ocr · LayoutQa · Classifier · Chunker · Indexer
│  │  ├─ extract/        pdf.py (pypdf) · docx.py (python-docx) · txt.py
│  │  └─ stub/           ocr.py · layout_qa.py · classifier.py · indexer.py
│  ├─ ai/
│  │  ├─ ports.py        AiEngine Protocol — answer() summarize() draft()   NFR-14
│  │  ├─ fixture_engine.py
│  │  ├─ scenarios.py
│  │  └─ fixtures/       answers · summaries · drafts · pages (ported from frontend mocks)
│  └─ seed.py            dev user + the 6-document fixture corpus
└─ tests/
   conftest.py · test_auth · test_conversations · test_documents_upload
   test_ingestion · test_sse_ask · test_sse_summary · test_sse_draft
   test_refusal · test_errors · test_hard_delete · test_audit · test_usage
   test_openapi_contract · test_layering
```

---

## 5. Build phases

Each phase ends in something **curl-able**. Check off as we go.

### B0 — Scaffold & the contract harness
- [x] `uv init` under `backend/`, Python 3.12 pinned; FastAPI, uvicorn, pydantic-settings, SQLAlchemy[asyncio], aiosqlite, alembic, argon2-cffi, cryptography, python-multipart, pypdf, python-docx, httpx, pytest, pytest-asyncio, ruff, mypy
- [x] `CamelModel` base: `alias_generator=to_camel`, `populate_by_name=True`, `extra='forbid'`; app-wide `response_model_by_alias=True`
- [x] App factory + `/healthz` + `/readyz`; OpenAPI 3.1 title/version/servers; CORS for `http://localhost:5173` with credentials
- [x] `Makefile`: `dev · test · lint · typecheck · migrate · seed · openapi`
- [x] **`scripts/export_openapi.py`** → `backend/openapi.json`, committed; `test_openapi_contract.py` asserts every path in §1 exists with the expected method and that no response model leaks snake_case
- **Curl-able:** `GET /healthz` and a complete `/openapi.json` with every §1 path declared.

### B1 — Persistence
- [x] All 16 tables as SQLAlchemy models; UUID7 string PKs; UTC-aware timestamps; cascade rules explicit
- [x] Alembic baseline migration; `make migrate` idempotent; WAL + `foreign_keys=ON` pragmas on connect
- [x] Async session dependency with per-request transaction and rollback-on-exception
- [x] `seed.py`: dev user `dana@example.com`, workspace, and the fixture corpus mirroring `mocks/documents.ts` — 312-page MSA, two opinions, a pleading, a scanned low-OCR contract, one `parsing`, one `failed`
- **Curl-able:** `make migrate && make seed` produces a populated `legallens.db`.

### B2 — Auth & the middleware chain — `FR-27, NFR-9`
- [x] `POST /api/auth/login`: argon2id verify, constant-time, generic failure message, server-side `sessions` row, `HttpOnly` `Secure` `SameSite=Lax` cookie, 14-day sliding expiry
- [x] `POST /api/auth/logout` revokes the row (not just the cookie); `GET /api/auth/me`
- [x] Middleware in architecture order: **request-id → timing/trace span → session auth → rate limit → validation → route → envelope → audit write**
- [x] Rate limiter: token bucket per session, separate budgets for turns / uploads / reads; `429` → `ApiError{kind:'rate_limited', retryAfterSec}` + `Retry-After`
- [x] Login brute-force throttle; every response carries `X-Request-Id`
- **Curl-able:** login sets a cookie, `/me` works with it, a 401 without it, a `rate_limited` ApiError after N rapid turns.

### B3 — Conversations, the NCC endpoint — `US-10`
- [x] All six conversation routes; `PATCH` accepts `{title?, pinned?}`
- [x] Opaque base64 cursor over `(pinned DESC, last_message_at DESC, id)`; `limit` capped at 50; `q` substring search on title
- [x] `GET /{id}` hydrates full `turns[]` from `messages` + `message_citations` + `envelope_json` into `UserTurn` / `AssistantTurn` exactly as `types.ts` declares
- [x] `dominantKind` and `turnCount` maintained on write, not computed per read
- [x] Soft delete for conversations (audit retention), hard delete for documents; 404 for another user's IDs — never 403, which confirms existence
- **Curl-able:** create → rename → pin → list (paged) → fetch with turns → delete.

### B4 — Documents: upload, validate, encrypted storage — `FR-1, FR-2, TS-4, NFR-9`
- [x] `POST /api/documents` multipart, multiple files in one request, streamed to disk — never buffered whole
- [x] Validation: magic-byte sniff for PDF/DOCX/TXT (extension and `Content-Type` are untrusted), >50 MB or >500 pages rejected **naming the limit and the actual value** (FR-2), 50-documents-per-workspace cap (NFR-4), zip-bomb and nesting guards for DOCX
- [x] Per-file outcome: `{accepted: LegalDocument[], rejected: [{name, reason}]}` — mirrors `RejectedFile` in `client.ts`, so one rejection never fails the batch
- [x] SHA-256 content hash → dedupe returns the existing document with `duplicateOf`
- [x] `blob.py`: AES-256-GCM, 96-bit random nonce per object, key from `LL_BLOB_KEY` (`.env.example` documents generation), content-addressed layout `blobs/{sha2}/{sha}.enc`
- [x] `signed_url.py`: HMAC, 60-second TTL, scoped to one object — originals and page images are never served from a guessable path
- [x] `GET /api/documents` with `type` / `status` filters and sort; `GET /{id}`; `PATCH /{id}` type override setting `typeSource='overridden'` (FR-5)
- **Curl-able:** upload a real PDF + an oversized file + a `.pptx` in one request and get three different, correctly-worded outcomes.

### B5 — Ingestion pipeline — `FR-3, FR-4, DR-1, NFR-2, NFR-5`
- [x] `ingestion_jobs` queue: claim-with-heartbeat, visibility timeout, exponential backoff, `attempt` cap of 3, crashed-job recovery on startup
- [x] asyncio worker pool, concurrency 3 (NFR-5), one job per file
- [x] Status machine `queued → parsing → (ocr) → indexing → ready | failed` with `progress` 0–100 and a `steps_json` log shaped as `IngestionStep[]` — the frontend's `DocumentDetailDrawer` already renders it
- [x] **Real extraction** (decision §7.1): `txt.py` straight read; `docx.py` paragraph+table walk via python-docx; `pdf.py` text-layer extraction via pypdf with **per-page char offsets** written to `document_pages` — this is what makes `FR-13` click-through real on a user's own file
- [x] Text-layer probe (chars/page heuristic) decides machine-readable vs. scanned; scanned pages go to the `Ocr` port, whose stub records `ocr_json{scanned, meanConfidence, lowConfidencePages}` so DR-1's UI warnings are exercised
- [x] `LayoutQa`, `Classifier`, `Chunker`, `Indexer` ports with stub implementations: filename+content-heuristic classifier matching `client.ts`'s rules, section-aware chunker writing real `chunk_meta` with `vector_point_id=NULL`, no-op indexer stamping `index_version`
- [x] Encrypted per-page text + a `spans` row per chunk boundary, so every fixture citation resolves against real stored text
- [x] `GET /{id}/status` poll endpoint; `POST /{id}/retry`; a deliberately corrupt fixture that fails and stays failed with the real message
- [x] Ingestion cost row in `usage_transactions` against the `$0.50/doc` budget (NFR-8)
- **Curl-able:** upload a 40-page PDF, poll `/status` through the lifecycle, then `GET /pages/7` and read its actual text.

### B6 — The AI port and the fixture engine — `NFR-14`
- [x] `AiEngine` Protocol — `answer(req) · summarize(req) · draft(req)`, each `AsyncIterator[StreamEvent]`; request/response models in `schemas/`, not in `ai/`
- [x] Port `mocks/answers.ts`, `summaries.ts`, `drafts.ts`, `pages.ts` to `app/ai/fixtures/` as Python data — same documents, same spans, same scripted answers, so the frontend's existing expectations hold byte-for-byte
- [x] `FixtureEngine`: real stage timings and jitter, real TTFT ≈ 600 ms (NFR-1), citations resolved against the **real** `spans` table for seeded documents and the fixture span map otherwise
- [x] Scope resolution server-side: workspace / subset / document, dropping non-`ready` documents **with a reason** into `scopeUsed.excluded` (FR-7, FR-9) — port of `computeScope`
- [x] Intent routing for `mode='auto'` by the same keyword rules the mock used, emitting `{type:'intent', intent, mode}` so `Auto → Ask` stays visible (frontend nuance #4)
- [x] `scenarios.py` honouring `X-LegalLens-Scenario`, disabled when `ENV=production`
- [x] `test_layering.py`: AST import scan asserting no module under `app/api/` or `app/services/` imports `app/ai/fixtures/`
- **Curl-able:** nothing yet — B6 is a library. Its proof is B7 plus the layering test.

### B7 — Turn endpoints and the stream — `FR-10, FR-14, NFR-7, TS-5`
- [x] `POST /api/conversations/{id}/messages` → SSE. Body `{text, mode, scope, draftParams?}` per `SendMessageRequest`
- [x] `POST /api/ask` · `/api/summarize` · `/api/draft` — same workflow, router skipped, `conversationId` optional (creates one)
- [x] `sse.py`: framing per §2, 15 s keep-alive, disconnect detection via `request.is_disconnected()`
- [x] Persist the user turn **before** streaming; persist the assistant turn **during** it (buffered, flushed on `done`); on disconnect or generator error write `status='incomplete'` with the partial text (NFR-7)
- [x] `Idempotency-Key` header: a replayed POST returns the stored turn rather than a second generation
- [x] Refusal path returns `200` with `{type:'refusal'}` and persists `status='refused'` (FR-14, TS-3)
- [x] Zero-ready-documents short-circuits to a refusal before any engine call — port of the mock's early return
- [x] `usage` + `versions` + `traceId` event, then `done` with the `disclaimer` from one server-side constant (TS-5) — the same string the frontend injects into exports, so they cannot drift
- [x] `traces` row, `usage_transactions` row and `audit_log` row written in the message's transaction (DR-4)
- [x] 60-second generation budget → `timeout` ApiError naming a narrower scope as the next action (FR-28)
- **Curl-able:** `curl -N` an ask, a summary and a draft and watch all thirteen event types; reload the conversation and see the turn replayed identically.

### B8 — Spans, pages, traces, feedback, usage, workspace — `FR-11, FR-13, FR-25, NFR-8, NFR-11`
- [x] `GET /api/spans/{spanId}` resolving to `{documentId, page, charStart, charEnd, section}`
- [x] `GET /api/documents/{id}/pages/{n}` returning decrypted page text + `ocrConfidence`; `ETag` + `Cache-Control: private, max-age=300` because the 500-page viewer will hammer this
- [x] `GET /api/documents/{id}/pages/{n}/image` → signed 60 s redirect
- [x] `POST /api/feedback` joined to the message **and its retrieval trace** (FR-25); re-submission updates
- [x] `GET /api/traces/{traceId}`: stages with timings, queries run, candidate IDs and scores, prompt/model/index versions (NFR-11, NFR-12)
- [x] `GET /api/usage` → `UsageLedgerRow[]` + workspace totals and budget headroom
- [x] `GET /api/workspace/health` → `WorkspaceHealth`
- **Curl-able:** every read surface the frontend's evidence panel, trace drawer and settings page need.

### B9 — Hard delete — `FR-26, TS-6, US-9`
- [x] `DELETE /api/documents/{id}`: `chunk_meta` → `spans` → `document_pages` → encrypted page text → page images → blob → `documents` row, in one transaction, with blob unlink after commit and a reconciliation sweep for orphans
- [x] Vector-point deletion as a port call, no-op in M3, so M4 adds one line
- [x] Immediate effect on retrieval: the document disappears from scope resolution in the same request
- [x] `DELETE /api/workspace` requiring `{confirm: "DELETE"}`; removes documents, conversations, traces, usage — **retains `audit_log` tombstones only**
- [x] **`test_hard_delete.py`**: upload → ingest → delete → assert every table empty of its ID, the blob path gone, the page text unreadable, the span 404, and an audit tombstone present. TS-6 says verified by test, so this test is the requirement
- **Curl-able:** delete a document and watch it vanish from `/documents`, `/spans`, `/pages` and scope at once.

### B10 — Errors, degradation, observability — `FR-28, NFR-6, NFR-11, NFR-12`
- [x] Global handlers mapping every failure to `ApiError` with a populated `action`: `llm_unavailable`, `rate_limited`, `parse_failed`, `timeout`, `network`; validation errors become `ApiError` too, never FastAPI's default `detail` array
- [x] **NFR-6 degraded mode:** when the engine port raises unavailable, retrieval and excerpts still stream and the error carries `excerptsAvailable: true` — the only way the product is useful during an outage, and the frontend's `DegradedBanner` already expects it
- [x] Structured JSON logging with `request_id` / `trace_id` / `user_id` and **a PII filter that refuses to log document text or page content** (DR-5)
- [x] OpenTelemetry spans per stage (retrieval → assembly → generation → verification) with cost attributed per stage, exporter off by default
- [x] `prompt_versions` / `index_versions` registries stamped on every generation (NFR-12)
- **Curl-able:** every `ApiErrorKind` reachable via the scenario header, each with a sensible `action` string.

### B11 — Tests, docs, runnable dev loop
- [x] pytest + `httpx.ASGITransport`, per-test SQLite file, fixtures for an authed client and a seeded workspace
- [x] SSE test helper parsing the stream into `StreamEvent` objects and asserting order and completeness
- [x] Coverage of: auth, pagination, the three streams, refusal, every ApiError kind, upload rejection wording, batch independence (one bad file among three), ingestion status machine, retry, idempotency replay, interrupted-stream persistence, hard delete, audit completeness, the layering rule
- [x] `ruff` + `mypy --strict` on `app/` clean in CI
- [x] `backend/README.md`: run in three commands, env var table, endpoint table, "what M4 replaces" section
- [x] `docker-compose.yml` for backend + frontend together; `Dockerfile` non-root, multi-stage
- [x] **Frontend integration note** (a short doc, not code): rewrite `client.ts` bodies to `fetch`, add `api/sse.ts`, delete `commitTurns`, delete `mocks/`, point the Demo menu at `X-LegalLens-Scenario`. Zero changes to `types.ts` or any component — which is the whole point of §0
- **Curl-able:** `make dev` in one terminal, `npm run dev` in the other, and the real frontend talking to the real backend.

---

## 6. Deliberately out of scope here

LangChain / LCEL orchestration · the intent-router LLM · query rewriting, hybrid retrieval, RRF, cross-encoder
rerank, parent expansion · Qdrant · real embeddings · the citation verification gate and groundedness check as
*real* checks (the port and its call site exist; the implementation is M4) · layout-aware parsing (Docling /
unstructured) · real OCR (Tesseract / PaddleOCR) · Celery/Redis · the CI eval harness (M5) · multi-tenancy ·
`.docx` export · Postgres.

Every one of these is a **port with a stub behind it**, not an absence. The stub's job is to make the status
machine, the envelope and the stream real today, so that M4 changes implementations and not interfaces.

---

## 7. Decisions I made, so this is not blocked on answers

Each is reversible; each is called out because it is the kind of thing that is annoying to change late.

**7.1 Real text extraction now, real OCR later.** TXT, DOCX and the PDF text layer are extracted for real
(pypdf, python-docx — pure Python, no system dependencies), with genuine per-page character offsets. Layout-aware
parsing and OCR stay stubs. *Why:* citation click-through (FR-13) is the product's trust surface, and it is only
truly tested when a span resolves against text pulled from a file the user actually uploaded. Stubbing extraction
would mean discovering offset bugs at M4, with the RAG pipeline on top of them. The expensive half — hostile
layout and OCR, BRD §15's highest risk — is correctly M4's problem.

**7.2 camelCase on the wire.** Pydantic alias generator; snake_case accepted on input. *Why:* `types.ts` already
says so, and the architecture's promise is that only `client.ts` changes at M3.

**7.3 SQLite now, Postgres as a URL change.** Async SQLAlchemy throughout, no SQLite-only DDL. *Why:* band 6 of
the architecture says exactly this, and WAL-mode SQLite comfortably serves NFR-5's ten users.

**7.4 A DB-backed asyncio queue, not Celery/Redis.** `ingestion_jobs` rows + an in-process worker pool behind a
`JobQueue` port. *Why:* FR-3's real requirement is per-file isolation and durable status, which a table gives and
an in-memory list does not. Celery buys distribution we do not need at 3 concurrent ingestions, at the cost of a
broker in `docker-compose` and in every developer's setup.

**7.5 The frontend's mocks move server-side rather than being rewritten.** Same fixture corpus, same spans, same
scripted answers, translated to Python. *Why:* the frontend was validated against those fixtures. Reusing them
makes the integration step a diff of transport only — and any divergence shows up immediately as a UI bug rather
than as an argument about which side is right.

**7.6 Session cookies, not JWTs.** FR-27 says server-side sessions; a revocable `sessions` row is also the only
honest way to implement logout in a confidentiality-bearing product.

---

## 8. Three things I'd flag before we start

1. **The fixture engine is load-bearing and will be tempting to over-build.** Its only job is to emit the right
   events in the right order with plausible timings. If it starts to acquire retrieval logic it becomes a second
   AI layer that M4 must then delete. I'm capping it at fixture lookup + timing, and the layering test in B6 is
   there to keep that honest.

2. **Per-page character offsets are the subtle part of B5.** `charStart`/`charEnd` in `types.ts` are offsets into
   *page* text, and `Excerpt.before`/`after` need surrounding context from the same page. Whether offsets are
   page-local or document-global has to be decided once, in B5, and asserted in a test — getting it wrong makes
   every highlight land in the wrong place, which looks like a frontend bug for a day. **I am going with
   page-local**, because `Span` already carries `page` and the viewer renders page-at-a-time.

3. **`commitTurns` disappearing is the one breaking change for the frontend.** Everything else in `client.ts` keeps
   its signature. Worth doing that deletion early in the integration pass rather than leaving a function that
   silently no-ops against a backend that is already persisting correctly.

---

---

## 9. Status — B0 → B11 complete

`make check` is green: ruff + `mypy --strict` + openapi freshness + **155 tests in 37s**.
33 operations across 26 paths. See `backend/README.md` for how to run it and what
M4 replaces.

### What changed from the plan, and why

1. **`summary_start` / `draft_start` already existed in `types.ts`.** I started
   adding an optional `meta` field to the field/section events to carry the
   document name and template name; the contract test showed `types.ts` already
   declares two dedicated header events for exactly that. Conformed to those
   instead — 15 `StreamEvent` variants, not 13.
2. **Nulls stay on the wire.** `exclude_none` would have stripped
   `valueMd: null`, which is how FR-16 distinguishes "not addressed in this
   document" from a field that was never sent.
3. **The engine is behind a provider, not constructed in a route.**
   `app/ai/engine.py` is the single M4 swap point; the layering test asserts
   nothing else builds an engine.
4. **§8.2 confirmed: span offsets are page-local**, asserted directly in
   `test_documents.py::test_span_offsets_are_page_local`.
5. **Three bugs found by the tests that would have shipped:** an async engine
   handed across event loops (worked cold, failed on a pooled connection); a
   module-level `asyncio.Event` bound to the first loop; and a JSON column
   mutated in place, so the ingestion log never reached the database.
6. **Dev encryption key is now stable on disk.** A per-process random key made
   every blob written by the previous process undecryptable — `make seed` then
   `make dev` served "authentication failed" for every page.
7. **Ingestion cost rows (NFR-8) were missing** until a test asked for them.

**Still deferred, as planned:** real OCR, layout-aware parsing, embeddings,
Qdrant, the real citation gate and groundedness check, the CI eval harness (M5),
Postgres, `.docx` export. Each sits behind a port with a stub that never invents
content — a PDF with no text layer is quarantined with a reason, not indexed
with fabricated text.
