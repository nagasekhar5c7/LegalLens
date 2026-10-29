# Legal Lens — Frontend ↔ Backend Integration Plan

**Scope of this document:** the integration pass only. The UI (M2, `FRONTEND-TODO.md`) and the API
(M3, `BACKEND-TODO.md`) are both built and both green. Nothing here adds a feature, a table, a screen or an
endpoint. It replaces a mock transport with a real one and settles the handful of places where the two sides
disagree. **No orchestration layer, no AI layer** — the fixture engine stays behind `app/ai/ports.py` and M4
remains a one-module swap.

**Stack touched:** `frontend/src/api/*`, `frontend/src/store/*`, seven components, one route guard, and
*four lines* of backend.

---

## 0. The two sides already agree — start from that, not from a rewrite

I diffed `frontend/src/api/types.ts` (392 lines of wire types) against `backend/openapi.json`
(33 operations, 52 schemas) field by field. The agreement is near-total, and it is worth stating
precisely, because it determines how big this job is:

| What | Status |
|---|---|
| Casing | camelCase on both sides. `answerMd`, `scopeUsed`, `lowConfidencePages`, `timeToFirstTokenMs`. **No mapping layer needed.** |
| `StreamEvent` | 15 variants, same names, same discriminator `type`, same payload field names, on both sides — including `summary_start` / `draft_start`. |
| Event order | `stage(routing) → intent → scope → […] → coverage? → usage → done`, identical to `mocks/stream.ts`. |
| `ApiError` (FR-28) | Same shape as an HTTP body *and* as a `{type:'error'}` event. One parser handles both. |
| Refusal | A `200` with a `refusal` object on both sides. The calm slate card stays reachable. |
| Fixture corpus | `doc_msa`, `doc_vance`, `doc_delmar`, `doc_tessara`, `doc_nda`, `doc_amendment`, `doc_depo`, `doc_exhibits` — **the same eight ids, the same names, the same span ids** (`s_msa_1223`, …). The backend seed is the frontend's mock corpus, ported. |
| Scenarios | `auto · refusal · llm_down · rate_limited · timeout · interrupted · slow` — identical seven, both sides. |
| Templates | `TEMPLATE_FIELDS` and `DRAFT_SECTIONS` keys/labels/headings are byte-identical in `mocks/summaries.ts` ↔ `ai/fixtures/summaries.py` and `mocks/drafts.ts` ↔ `ai/fixtures/drafts.py`. |
| Type-to-confirm | The dialog types `DELETE`; the backend requires `{"confirm":"DELETE"}`. Already matching. |
| CORS | `allow_credentials=True`, origin `http://localhost:5173`, and `Idempotency-Key` + `X-LegalLens-Scenario` already in `allow_headers`. |

**Consequence:** this is a transport swap plus the fifteen deltas in §2. The fifty-odd components that
consume `@/api/types` do not change at all — which is exactly the promise `FRONTEND-TODO.md` §"one file
changes when the backend lands" made, and it mostly holds. It is *five* files, not one, and §2.1–2.15 say why.

**The best proof that this worked:** `/doc/doc_msa?page=84&span=s_msa_1223` must still land on the same clause
after the swap, now served from SQLite and an AES-GCM blob instead of a generator in the browser.

---

## 1. Endpoint reconciliation — every `client.ts` function, resolved

Thirty-three backend operations; `client.ts` exposes twenty functions. The mapping, in full:

| `client.ts` | Backend | Delta |
|---|---|---|
| `validateFile(file)` | `GET /api/config` | **Limits come from the server.** Today it reads `LIMITS` from `@/mocks/documents`. §2.5 |
| `listConversations()` | `GET /api/conversations` | Returns `{items, nextCursor}`, not an array. Unwrap `.items`; keep the signature. §2.13 |
| `getConversation(id)` | `GET /api/conversations/{id}` | Exact match. 404 → `null`. |
| `createConversation(title)` | `POST /api/conversations` → **201** | Exact match. The wrapper must accept any 2xx. |
| `renameConversation(id,t)` | `PATCH /api/conversations/{id}` | Body `{title}`; returns `ConversationSummary`. Adopt it instead of patching locally. |
| `setPinned(id,p)` | `PATCH /api/conversations/{id}` | Body `{pinned}`. Same call, same handler. |
| `deleteConversation(id)` | `DELETE /api/conversations/{id}` → 204 | Exact match. Soft delete server-side, invisible to the client. |
| `sendMessage(req,opts)` | `POST /api/conversations/{id}/messages` | **SSE over POST.** `EventSource` is GET-only, so `fetch` + `ReadableStream`. §2.1 |
| — | `POST /api/ask` · `/summarize` · `/draft` | Unused by the UI; the composer always has a conversation. Keep them for curl/M0 parity, do not wire. |
| `listDocuments()` | `GET /api/documents` | Exact match (array, not paged). |
| `ingest(file, onUpdate)` | `POST /api/documents` + `GET /api/documents/{id}/status` | **Rewritten.** Multi-file multipart, then poll. §2.3 |
| `setDocumentType(id,t)` | `PATCH /api/documents/{id}` | Returns the updated `LegalDocument`. Adopt it; drop the re-list. |
| `deleteDocument(id)` | `DELETE /api/documents/{id}` → 204 | Exact match. Real hard delete (FR-26). |
| `deleteWorkspace()` | `DELETE /api/workspace` → 204 | Needs body `{"confirm":"DELETE"}`. |
| `retryIngest(id,onUpdate)` | `POST /api/documents/{id}/retry` | Returns `DocumentStatus`; then poll. §2.3 |
| `getPage(id,n)` | `GET /api/documents/{id}/pages/{n}` | Async now. Carries `ETag` + `private, max-age=300`. §2.4 |
| `getSpan(spanId)` | `GET /api/spans/{spanId}` | Async; 404 → `null`. §2.4 |
| `submitFeedback(turnId,f)` | `POST /api/feedback` → 204 | **Needs the server's turn id**, not the client's `a_…`. §2.2 |
| `getUsage()` | `GET /api/usage` | Returns `UsageLedger` (rows **+ server-computed totals**). §2.8 |
| `login(email,pw)` | `POST /api/auth/login` | 200 → `{ok,email,workspaceName}`; 401 → `ApiError` body. §2.6 |
| — | `POST /api/auth/logout` · `GET /api/auth/me` | **Not called today.** The app has no auth gate at all. §2.6 |
| `commitTurns(...)` | — | **Deleted.** The server persists each turn as it streams. §2.7 |
| — | `GET /api/workspace/health` | Health is derived client-side from `documents`. Keep derived; adopt the endpoint as the source of truth on load. §2.15 |
| — | `GET /api/traces/{traceId}` | `TraceDrawer` renders from the local turn. Optional enrichment: queries + candidate scores. §2.15 |
| — | `GET /api/turns/{turnId}` | Reconnect probe: did a stream I lost actually finish? Optional. §2.15 |
| — | `GET /api/documents/{id}/pages/{n}/image` | **404s until M4** — no page rasteriser yet. Do not wire. §2.15 |
| — | `GET /healthz` · `/readyz` | Ops only. The URL builder must not prefix these with `/api`. |

---

## 2. The fifteen deltas — what is actually work

### 2.1 SSE over POST is a hand-written parser, not `EventSource`

`EventSource` cannot POST, and the request body carries `scope`, `mode` and `draftParams`. So:
`fetch(..., {method:'POST', signal})` → `res.body.getReader()` → a line parser that buffers partial chunks,
splits on `\n\n`, honours `data:` (possibly multi-line), ignores `:` keep-alive comments and the opening
`retry: 3000`, and `JSON.parse`s the payload — whose `type` field is the discriminator, which is why the
backend writes it *inside* the JSON as well as on the `event:` line.

Four things that will bite if they are not handled in the parser itself:
- **A non-200 is not a stream.** A 429 or 401 arrives as a JSON `ApiError` body. Parse it and yield one
  `{type:'error', error}` so `useConversations` needs no second code path.
- **Chunk boundaries fall mid-event.** Keep a string buffer across reads; never assume one read is one frame.
- **Keep-alives arrive every 15 s** during a long summary (NFR-3). A parser that chokes on `:` lines will
  break exactly the three-minute case the keep-alive exists for.
- **`valueMd: null` is load-bearing.** The backend deliberately does not `exclude_none` — null means
  "not addressed in this document" (FR-16). Do not coalesce it to `''` anywhere in the pipeline.

### 2.2 Turn identity — the frontend invents ids the server has never heard of

`useConversations.send()` mints `u_<ts>` / `a_<ts>` locally. The server mints its own UUID7 message ids and
persists both rows *before the first byte streams*. Nothing in the stream tells the client what they are.
That breaks exactly one thing: `POST /api/feedback` takes `turnId` and 404s on `a_m1k2j3`.

**Fix (the only backend change in this plan):** have the SSE response carry the two ids as headers, and
expose them through CORS. Four lines, no new event variant, no contract change, and feedback works the
instant the turn completes without a refetch.

```python
# app/api/routes/turns.py — _stream_turn already calls persist_user_turn, which
# returns the Message; the call site currently discards it.
user_turn = await turn_service.persist_user_turn(...)
...
return StreamingResponse(body_stream(), media_type=MEDIA_TYPE, headers={
    **SSE_HEADERS,
    "X-LegalLens-Turn-Id": assistant_id,
    "X-LegalLens-User-Turn-Id": user_turn.id,
})
# app/main.py — CORSMiddleware
expose_headers=[..., "X-LegalLens-Turn-Id", "X-LegalLens-User-Turn-Id"]
```

`api.sendMessage` then takes an `onIds` callback (or yields a synthetic first event) and the store rewrites
the two optimistic turn ids before the first token lands.

*Rejected alternative:* re-`GET /api/conversations/{id}` when the stream ends and swap in the persisted turns.
It needs no backend change and is a fine backstop, but it replaces the whole turn list at the moment the user
starts reading, and it loses `expectedFieldCount` (client-only). Keep it as the reconnect path (§2.15), not
as the primary.

### 2.3 Upload: a per-file simulation becomes one multipart POST and a poll

Today `api.ingest(file, onUpdate)` walks a fake lifecycle and pushes progress into a callback. The real shape
is different in three ways: the endpoint is **multi-file in one request** (`files` is a repeated field),
rejections come back as `rejected[]` **alongside** `accepted[]` in a 201 (one bad file never fails the batch,
FR-3), and progress is **read by polling**, not pushed.

`useWorkspace.upload()` is rewritten as: client-side `validateFile` → one `POST /api/documents` with every
accepted file → map `accepted[]`/`rejected[]` onto the existing `UploadRow[]` → poll until every accepted
document reaches `ready` or `failed`.

Three sharp edges:
- **`fetch` has no upload progress.** The transfer phase is indeterminate; the real 0–100 comes from the
  ingestion poll. If a byte-level bar is wanted, `XMLHttpRequest` is the only option — not worth it for M3.
- **Duplicate detection returns an existing document.** The backend sha256s and returns the already-ingested
  row, with no new job. The poll must terminate immediately on a terminal status or it will spin forever on a
  document that was `ready` before the upload started.
- **Poll the list, not N statuses.** `GET /api/documents/{id}/status` is designed as the poll target, but a
  dropped batch of eight files polled at 1 s is 480 reads/min against a 600 reads/min limit. Poll
  `GET /api/documents` **once per tick** while any document is in-flight (one request, full `LegalDocument`
  including the ingestion step log), at 1500 ms with jitter, and stop when nothing is in-flight. Use
  `/status` only on `DocumentPage`, where exactly one document matters.

### 2.4 The document viewer is synchronous and the API is not

`DocumentViewer` calls `getPage(doc.id, n)` inline in render for a ±2-page window, and `resolveSpan(spanId)`
in a `useMemo`. Both become network calls. `PageSurface` already accepts `page: Page | null`, so the
rendering side is ready — what is missing is a cache.

Add `usePages(documentId)`: a `Map<number, Page>` + in-flight set, fetch-on-demand for the mounted window,
`AbortController` per page so a fast scroll through a 312-page MSA cancels what it flew past, and `null` for
not-yet-loaded (the skeleton path that already exists). The `ETag` + `private, max-age=300` the endpoint
already sends means a re-visit is a 304.

### 2.5 Limits must come from `/api/config`, not from `mocks/documents`

FR-2 requires the rejection message to name the real limit. `LIMITS` in `mocks/documents.ts` happens to match
`Settings` today — a coincidence that survives exactly until someone sets `LL_MAX_UPLOAD_BYTES`. Add a tiny
`useConfig` store, load it once at boot alongside `me`, and have `validateFile`, `UploadDropzone` (the
`accept` attribute and the helper line) and `LibraryPage` (the document-limit line) read from it. Ship a
conservative fallback so a config fetch failure degrades to today's constants rather than to a broken
dropzone.

`scenariosEnabled` rides along in the same payload: when the backend reports `false` (i.e. `LL_ENV=production`)
the Demo menu hides itself instead of sending a header the server ignores.

### 2.6 There is no auth gate

`LoginPage` exists, `api.login` validates an email client-side and returns `{ok:true}` for any password of
eight characters, and **no route is protected**. Every real endpoint except `/api/config`, `/healthz`,
`/readyz` and `/api/auth/login` needs the `ll_session` cookie.

Needed: a `useSession` store (`status: 'unknown' | 'authed' | 'anon'`, `email`, `workspaceName`), a
`bootstrap()` that calls `GET /api/auth/me` once before the shell renders, a `<RequireAuth>` wrapper around
every route but `/login`, a logout action wired to `POST /api/auth/logout`, and — the part that is easy to
forget — **a 401 handler in the HTTP wrapper** that flips the session to `anon` and redirects, so a session
that expires mid-afternoon does not surface as seven toasts saying "network error".

Dev credentials from `make seed`: `dana@example.com` / `counsel-demo-2026`. `LoginPage` currently prefills
`dana@vancelaw.example`, which will fail — update the placeholder.

### 2.7 `commitTurns` is deleted, not implemented

Flagged in `BACKEND-TODO.md` §8.3 as the one breaking change, and it is. The server persists the user turn and
the assistant turn before streaming, accumulates the envelope as it goes, and writes the final status in a
`finally` — `complete`, `refused`, `error` or `incomplete` (NFR-7). A client that pushes its own state back
would be overwriting the truth with a guess. Delete the function and the call in `send()`'s `finally`.

### 2.8 `getUsage` returns a ledger, not rows

Backend `UsageLedger` = `{rows, totalUsd, totalTokens, totalAsks, meanUsdPerAsk, budgetPerAskUsd}`.
`SettingsPage` currently sums the rows itself. Add `UsageLedger` to `types.ts`, return it whole, and let the
page render the server's totals — they are computed over all rows, which a client-side sum over a partial page
would not be, and `budgetPerAskUsd` is the $0.05 figure (NFR-8) the page is comparing against.

### 2.9 `src/mocks/` holds real UI constants that must survive its deletion

Seven non-`client.ts` modules import from `@/mocks`, and most of them are not asking for mock *data* — they
want presentational skeletons:

| Import | Used for | New home |
|---|---|---|
| `TEMPLATE_FIELDS` | `SummaryView`'s pending-field skeletons | `src/lib/templates.ts` |
| `DRAFT_SECTIONS`, `DRAFT_TYPE_LABEL` | `DraftView` pending sections, `UserMessage` badge | `src/lib/templates.ts` |
| `AUDIENCE_OPTIONS`, `TONE_OPTIONS`, `POSITION_OPTIONS` | `DraftParamsDrawer` selects | `src/lib/templates.ts` |
| `SCENARIOS`, `type Scenario` | `DemoMenu`, `useUi` | `src/lib/scenarios.ts` |
| `titleFromQuestion` | `useConversations.send` | `src/lib/format.ts` |
| `getPage`, `resolveSpan` | `DocumentViewer` | **gone** → `api.getPage` / `api.getSpan` |
| `buildCitations`, `buildExcerpts`, `refusalFor` | `KitchenSinkPage` only | `src/dev/fixtures.ts` |

Then `src/mocks/` is deleted in one commit. Note that `DraftPayload`'s `draft_start` event carries no section
count, so `DRAFT_SECTIONS` genuinely stays as the client's expectation of what is coming — that is a UI
constant, not a mock, and it matches the fixture engine exactly.

### 2.10 Stop means abort, or the record lies

`stop()` adds the turn id to a `cancelled` set and `break`s the `for await`. Against a real stream that leaves
the socket open and the server believing the client is still reading. Thread an `AbortController` through
`sendMessage`; `stop()` aborts it. The server's `request.is_disconnected()` then fires, `with_keepalive`
returns, and `_finalise` writes `status='incomplete'` — which is what NFR-7 is actually about: what the
*record* says, not only what the UI paints.

### 2.11 The scenario header, so the Demo menu survives integration

`useUi.scenario` → `X-LegalLens-Scenario: <scenario>` on every turn request, omitted when `auto`. The backend
honours exactly the seven the frontend defines and ignores the header outright when `LL_ENV=production`.
This is the whole reason the frontend's failure-mode validation (P7) does not die at integration: every
`ApiError` kind, the refusal card, the interrupted-stream path and the slow-stage trace stay one menu click
away, now exercised end to end.

### 2.12 Idempotency on send — one key per send, never reused on regenerate

`POST /api/conversations/{id}/messages` honours `Idempotency-Key` and returns the stored turn as JSON with
`X-LegalLens-Replay: true` instead of generating (and charging for) a second one. Mint
`crypto.randomUUID()` per `send()` invocation. **`regenerate()` must mint a fresh one** — reusing the key
would replay the old answer, which is the precise opposite of what the button means. The wrapper must also
handle the replay case: a JSON body where a stream was expected.

### 2.13 Conversation list is paged

`{items, nextCursor}` with `?cursor=&limit=&q=`. Unwrap `.items` now and keep `listConversations()`'s
signature; wire `nextCursor` into the sidebar's infinite scroll and `q` into the command palette's search in
I8 — `FRONTEND-TODO.md` already parked both as M3 work.

### 2.14 Both sides auto-title the first message — let the server do it

`createConversation(titleFromQuestion(text))` already sends the title, so the backend's `autotitle` correctly
no-ops (it only fires on a title of `"New chat"`). But `send()` has a second branch: when the conversation
exists and `turns.length === 0` it `PATCH`es a title *before* opening the stream — a round trip on the
critical path to the first token, for something `persist_user_turn` → `autotitle` does server-side in the same
transaction as the message. Delete that branch and let the server name the thread; adopt the title from the
`ConversationSummary` the next `loadList()` returns.

### 2.15 Deliberately not wired

- **Page images** (`/pages/{n}/image`) 404 by design until M4 brings a rasteriser. `PageSurface` keeps
  rendering text with offset-computed highlights; the span maths is already real, which was the point.
- **`GET /api/traces/{traceId}`** — `TraceDrawer` renders stages, usage and versions from the turn it already
  has. The endpoint adds the queries actually run and candidate scores. Nice, optional, I9.
- **`GET /api/turns/{turnId}`** — the reconnect probe. Only matters once the app survives a refresh
  mid-stream. I9.
- **`GET /api/workspace/health`** — `workspaceHealth(documents)` is a pure function over the loaded list and
  is correct while the list is complete. Call the endpoint on load for truth; keep the derived value as the
  live path so the card moves during an upload without a round trip.

---

## 3. Files

```
frontend/src/
├─ api/
│  ├─ env.ts          ⊕ base URL resolution + typed import.meta.env
│  ├─ http.ts         ⊕ fetch wrapper: credentials, 2xx/204, ApiError, 401, abort, headers
│  ├─ sse.ts          ⊕ POST + ReadableStream line parser → AsyncGenerator<StreamEvent>
│  ├─ client.ts       ↻ same signatures, real bodies; commitTurns deleted, ingest → uploadDocuments
│  └─ types.ts        ↻ + UsageLedger, + ServerConfig, + Me  (nothing existing changes)
├─ store/
│  ├─ useSession.ts   ⊕ me · login · logout · bootstrap
│  ├─ useConfig.ts    ⊕ server limits + scenariosEnabled
│  ├─ useConversations.ts ↻ abort, server ids, no commitTurns, idempotency key
│  └─ useWorkspace.ts ↻ multipart upload + list polling
├─ components/
│  ├─ auth/RequireAuth.tsx  ⊕
│  ├─ viewer/DocumentViewer.tsx ↻ async pages via usePages
│  ├─ documents/UploadDropzone.tsx ↻ limits from config
│  ├─ shell/DemoMenu.tsx    ↻ hidden when !scenariosEnabled
│  ├─ shell/TopBar.tsx      ↻ logout action
│  └─ answer|chat|composer/… ↻ import moves only (§2.9)
├─ hooks/usePages.ts ⊕ page cache + abort
├─ lib/{templates,scenarios}.ts ⊕   dev/fixtures.ts ⊕
├─ pages/{LoginPage,SettingsPage,LibraryPage}.tsx ↻
├─ mocks/            ✗ deleted
└─ App.tsx           ↻ RequireAuth + session bootstrap gate

frontend/{vite.config.ts, .env.example, src/vite-env.d.ts}  ↻ dev proxy + typed env
backend/app/{api/routes/turns.py, main.py}                  ↻ two response headers (§2.2)
```

---

## 4. Phases

Each phase ends in something **clickable in the browser against a live backend**. Run
`cd backend && make seed && make dev` once first; `localhost:8000/docs` is the reference while working.

### I0 — Transport plumbing
- [x] `vite.config.ts`: dev proxy `^/(api|healthz|readyz)` → `http://127.0.0.1:8000`, `changeOrigin: false`
- [x] `src/api/env.ts`: `API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''` — empty means "same origin via
      the proxy", which is what `docker-compose.yml` already overrides with `http://localhost:8000`
- [x] `vite-env.d.ts`: `interface ImportMetaEnv { readonly VITE_API_BASE_URL?: string }`; `frontend/.env.example`
- [x] `src/api/http.ts`: `request<T>()` — `credentials:'include'`, JSON in/out, 204 → `undefined`, any 2xx OK,
      non-2xx → `throw new ApiErrorResponse(body as ApiError, status)`, 401 → `useSession.signOut()`,
      `AbortSignal` passthrough, optional `Idempotency-Key` / `X-LegalLens-Scenario`
- **Verify:** a console `request('/api/config')` returns the real limits; `/healthz` is reachable; the
  response has no CORS error with the proxy and none without it either.

> **Why a dev proxy.** Same-origin removes every cookie and CORS question from the loop at once. The backend's
> `SameSite=Lax` cookie *would* ride along on `localhost:5173 → localhost:8000` (port is not part of a "site"),
> but it will **not** if the browser is on `127.0.0.1:5173` and the API on `localhost:8000` — different hosts,
> no cookie, a login that looks successful and an app that 401s on everything. The proxy makes that class of
> bug impossible locally; `VITE_API_BASE_URL` keeps the compose/prod path cross-origin and honest.

### I1 — Auth, so everything after this can assume a session — `FR-27`
- [x] `useSession`: `bootstrap()` → `GET /api/auth/me`; `signIn(email,pw)`; `signOut()`
- [x] `api.login` → real `POST /api/auth/login`; 401's `ApiError.message` becomes the form error
- [x] `RequireAuth` + App.tsx gate; `status==='unknown'` renders the existing `RouteFallback`, not a redirect
      (a redirect-then-bounce on every cold load is the classic flicker here)
- [x] Logout in `TopBar`; `LoginPage` prefill → `dana@example.com`
- [x] 401 from any later call → `signOut()` + `/login`, with the return path preserved
- **Verify:** `/` redirects to `/login` when signed out; the seeded credentials land on the chat shell; a
  `make reset` mid-session bounces cleanly to login instead of toasting errors.

### I2 — Reads: conversations, documents, config
- [x] `listConversations` (unwrap `.items`), `getConversation`, `createConversation`, `renameConversation`,
      `setPinned`, `deleteConversation`
- [x] `listDocuments`, `setDocumentType` (adopt the returned document), `deleteDocument`, `deleteWorkspace`
      (`{confirm:'DELETE'}`)
- [x] `useConfig` + `validateFile` from server limits; `UploadDropzone` and `LibraryPage` read the store
- [x] `getUsage` → `UsageLedger`; `SettingsPage` renders server totals against `budgetPerAskUsd`
- [x] Sidebar health: `GET /api/workspace/health` on load, derived value stays live
- **Verify:** the sidebar lists the seeded conversations, the library shows the eight seeded documents with
  their real statuses (one `parsing`, one `failed`, the scanned amendment with its OCR warning), the settings
  ledger shows real spend, and a 404 on a deleted conversation lands on a new chat rather than a blank pane.

### I3 — The stream — `FR-10, FR-14, NFR-7, TS-5`
- [x] `src/api/sse.ts`: buffered line parser; `data:` (multi-line), `:` comments, `retry:`; non-200 → one
      `error` event; `AbortSignal`; `finally { reader.releaseLock() }`
- [x] `api.sendMessage(req, {scenario, signal, onIds, idempotencyKey})`
- [x] Backend: the two turn-id headers + `expose_headers` (§2.2)
- [x] `useConversations.send`: abort controller, adopt server ids via `onIds`, fresh idempotency key,
      `commitTurns` call deleted
- [x] `stop()` aborts; `regenerate()` mints a new key
- [x] Drop the pre-stream `renameConversation` branch; the server auto-titles (§2.14)
- [x] `api.submitFeedback` with the server turn id
- **Verify:** ask "What happens on a change of control?" over the seeded MSA — tokens stream, citation chips
  appear in order (never before the `scope` event that justifies them), the evidence panel fills live, the
  usage row and disclaimer land, and **a reload shows the same turn from the database**. Thumbs-down persists:
  `sqlite3 legallens.db 'select * from feedback'`.

### I4 — Upload and ingestion — `FR-1, FR-2, FR-3`
- [x] `api.uploadDocuments(files)` → one multipart POST, `{accepted, rejected}`
- [x] `api.retryIngest(id)` → `POST /api/documents/{id}/retry`
- [x] `useWorkspace.upload`: validate → POST → map rows → single-flight list poll at 1500 ms + jitter, stop
      when nothing is in-flight, terminate immediately on a duplicate's terminal status
- [x] Ingestion step log and failure reason/detail read from the real `LegalDocument`
- **Verify:** drop a real PDF, a real DOCX and a `.pptx` together — two ingest to `ready` with a genuine page
  count and a real step log, the third is rejected by name with the real limit, and the batch does not fail.
  Re-drop one of the two: it dedupes to the existing document instead of ingesting twice. Retry the seeded
  `failed` document and watch it fail again with the server's reason.

### I5 — Pages and spans — `FR-4, FR-11, FR-13`
- [x] `api.getPage`, `api.getSpan`
- [x] `usePages(documentId)`: window fetch, cache, abort-on-scroll-past
- [x] `DocumentViewer` consumes it; `PageSurface`'s `null` path is the loading state
- **Verify:** `/doc/doc_msa?page=84&span=s_msa_1223` lands on the right page with the highlight on the right
  characters — the page-local offset decision from `BACKEND-TODO.md` §8.2 proven from the UI. Scroll fast
  through 300 pages: no request storm, no stale page painted into the wrong slot.

### I6 — Failure modes end to end — `FR-28, NFR-6, NFR-7`
- [x] Demo menu sends `X-LegalLens-Scenario`; hidden when `!scenariosEnabled`
- [x] Every `ApiErrorKind` renders in `ErrorCard` from a real response: `llm_unavailable`, `rate_limited`
      (with `retryAfterSec`), `timeout`, `parse_failed` (with `documentId`), `network`
- [x] `excerptsAvailable` keeps the evidence panel readable through an outage (NFR-6)
- [x] Interrupted stream → `incomplete` in the UI **and** in `messages.status`
- [x] Rate limit: 21 sends in a minute shows the real 429 with its `Retry-After`
- **Verify:** every one of the seven scenarios, clicked, against the real backend. Then
  `sqlite3 legallens.db "select status from messages order by created_at desc limit 5"` — the statuses match
  what the screen said.

### I7 — Delete `src/mocks/`
- [x] Move the seven constant groups per §2.9; `src/dev/fixtures.ts` for `KitchenSinkPage`
- [x] `rm -rf src/mocks`; `npm run typecheck && npm run lint && npm run build` clean
- [x] `grep -rn "@/mocks" src/` returns nothing
- **Verify:** the build passes and the kitchen sink still renders every state.

### I8 — Paging and search
- [x] Sidebar infinite scroll on `nextCursor`
- [x] Command palette search → `?q=`
- [x] `GET /api/conversations/{id}/messages?cursor=` for long threads
- **Verify:** a workspace with 60 conversations scrolls without loading all of them.

### I9 — Polish, optional endpoints, docs
- [x] `TraceDrawer` optionally fetches `GET /api/traces/{traceId}` for queries + candidate scores (NFR-11)
- [x] `GET /api/turns/{turnId}` reconnect probe: a refresh mid-stream resolves to the persisted turn
- [x] `docker compose up --build` works from a clean checkout, `LL_BLOB_KEY` documented
- [x] `frontend/README.md`: how to run against a local backend; root `README.md` quickstart
- **Verify:** a fresh clone reaches a cited answer in under five minutes with two commands.

### 2.16 Found during the work: Stop did not record the turn at all

Not in the plan, because I did not know it. `_finalise` was awaited inside
`body_stream`'s `finally`, and under cancellation that is unreachable: Starlette cancels the task the
moment it sees the disconnect, so the first `await` in the `finally` re-raises the `CancelledError`
that put us there and the write never happens. The turn stayed `streaming` for ever — a row claiming
to be in progress for a generation that had stopped, with the partial text lost.

It survived the existing suite because the `interrupted` *scenario* is a different path: the engine
emits an `ErrorEvent` and the generator ends normally, so the `finally` is never entered under
cancellation. Nothing exercised a real client hang-up, which is exactly what the Stop button now
does.

Fixed by detaching the write with `asyncio.shield`, keeping the `await` for the ordinary path so a
client that reads `done` and immediately re-reads the conversation still sees a durable turn, and
holding the task in a module-level set because the loop keeps only weak references to tasks.
`tests/test_errors.py::test_client_disconnect_persists_as_incomplete` drives ASGI directly to
reproduce it — httpx's transport drains the response rather than cancelling it, so through
`AsyncClient` the stream runs to completion and records `complete`, the opposite of the condition
under test.

---

## 5. Decisions I made so this is not blocked on answers

**5.1 A Vite dev proxy, with `VITE_API_BASE_URL` as the override.** *Why:* it eliminates the
`127.0.0.1`-vs-`localhost` cookie trap (§I0) and every preflight, while keeping the cross-origin path that
`docker-compose.yml` already configures exercised in compose. Cookies stay `HttpOnly` — the SPA never reads a
token, which is the point of FR-27's server-side sessions.

**5.2 Two response headers rather than a new stream event or a post-stream refetch.** *Why:* the ids exist
before the first byte; a header is the cheapest honest place to put them. A new event variant would change a
contract both sides and 155 tests already agree on, for information that is not really an event.

**5.3 Poll `GET /api/documents` once per tick, not `/status` per document.** *Why:* one request instead of N
against a 600 reads/min limit, and the list response carries the full ingestion step log the detail drawer
wants anyway. `/status` keeps its purpose on `DocumentPage`.

**5.4 Keep `client.ts` as the only seam.** *Why:* it is the architecture's promise and the reason fifty
components are untouched by this plan. Stores call `api.*`; nothing else calls `fetch`. The two new modules
(`http.ts`, `sse.ts`) sit *under* `client.ts`, not beside it.

**5.5 Optimistic turns stay, server ids are adopted.** *Why:* the user's message and the streaming shell must
paint instantly; waiting for a round trip to render your own sentence is a visible regression from the mock.

**5.6 `src/mocks/` is deleted, not kept behind a flag.** *Why:* the fixtures live server-side now
(`app/ai/fixtures/`, same corpus, same spans). Two copies of a fixture corpus is two sources of truth, and
the next divergence becomes an argument about which side is right.

**5.7 Client-side `validateFile` stays, with server-sourced limits.** *Why:* FR-2 wants the message instantly,
and the server re-validates regardless — including a magic-byte sniff the browser cannot do. Client validation
is UX; the server's is the control.

---

## 6. Three things I would flag before starting

1. **The ingestion poll is the easiest place to write a bug that only shows up on a slow machine.** A
   `setInterval` that fires while the previous request is still out, on a component that unmounts when the user
   navigates to the library's detail drawer, is the classic version. Single-flight it, drive it from one place
   (`useWorkspace`), and make the stop condition "nothing in-flight" rather than a fixed number of ticks — a
   312-page MSA takes longer than a 2-page NDA and a duplicate takes zero.

2. **Turn-id adoption has a race worth naming.** Headers arrive with the response, before the first token, so
   `onIds` fires early — but `send()` has already pushed the optimistic turns into the store and the user may
   have clicked thumbs-up on a *previous* turn in between. Rewrite by matching on the optimistic id, never by
   index, and make the rewrite idempotent.

3. **`npm run build` runs `tsc --noEmit` first, so this stays honest by construction.** Deleting
   `commitTurns` and `src/mocks/` will break compilation in exactly the places that need attention, which is
   the cheapest possible test. Worth running `npm run typecheck` after *every* phase rather than at the end —
   and worth doing I7's deletion before I8, so no new code is written against a mock that is about to vanish.

---

## 7. Out of scope here

PDF.js rendering and page images (M4, with the rasteriser) · real retrieval, embeddings and the citation gate
(M4, behind `app/ai/ports.py`) · `.docx` export (BRD §3) · dark theme (tokens ready) · multi-user and roles ·
Postgres (a URL change, M6) · an E2E test suite — this pass is validated by clicking every scenario in the
Demo menu against a live backend, the same way M2 was validated against the mocks, plus the `sqlite3` checks
named in each phase. Backend `make check` (ruff · mypy --strict · openapi freshness · 155 tests) must stay
green: the only backend diff in this plan is §2.2's four lines, and `scripts/export_openapi.py --check` will
notice if anything else moves.

---

## 8. Status — I0 → I9 complete

`npm run build` (tsc --noEmit + production build) and `npm run lint` are clean. `src/mocks/` is
deleted. Backend `make check` is green: ruff, `mypy --strict`, openapi freshness and **156 tests** —
155 that were already there plus one regression test for §2.16.

### Verified against a running backend, not asserted

| What | How |
|---|---|
| **The SSE parser is exact** | The real event stream, captured from the backend, replayed through `src/api/sse.ts` at chunk sizes 1, 3, 17, 64, 997 and 1 MiB: **141 events and 1504 characters of answer text, identical at every size**, with a keep-alive comment and the opening `retry:` hint spliced in and correctly skipped. |
| **Event order is contractual** | `stage → intent → scope → stage… → excerpt → token/citation → usage → done`. No citation arrives before the `scope` that justifies it. |
| **Turn ids reach the client** | `X-LegalLens-Turn-Id` / `X-LegalLens-User-Turn-Id` present on the stream, in the CORS expose list, and surviving the Vite proxy hop. Feedback with the header's id → 204 and a `feedback` row carrying the trace id; feedback with a client-invented `a_…` id → 404, which is the whole reason the headers exist. |
| **Every failure path is real** | All five error scenarios driven through the header: `refusal` (`below_threshold`, 3 queries named), `llm_down` (`llm_unavailable`, `excerptsAvailable: true`), `rate_limited` (`retryAfterSec: 24`), `timeout`, `interrupted`. |
| **Rate limiting** | 25 concurrent turns against the 20/min limit → a 429 whose body is an `ApiError` with `retryAfterSec: 2` and a matching `Retry-After` header. |
| **NFR-7, both ways** | Server-side interruption and a real client hang-up both recorded as `incomplete` with the partial text kept and FR-24's `INCOMPLETE OUTPUT` marking inside the stored text. `select status, count(*) from messages` shows `complete · error · incomplete · refused` — the screen and the record agree. |
| **Upload is per-file** | A `.txt` and a `.pptx` dropped together: one ingested to `ready` through its six real pipeline steps, the other rejected by name with the real limit, batch unaffected. Re-dropping the same file deduped by SHA-256 to the existing `ready` document — the case that would have made a status poll spin for ever. |
| **FR-11 offsets** | `GET /api/spans/s_msa_1223` → page 84, chars 420–970; `GET /api/documents/doc_msa/pages/84` → 1201 characters, and `text[420:970]` is the assignment clause. Page-local, as `BACKEND-TODO.md` §8.2 decided, proven from the client's side. |
| **The trace drawer has something to show** | `GET /api/traces/{id}` returns the 6 queries actually run and 3 scored candidates with `kept` flags. |
| **The dev proxy** | `/api/config`, `/healthz` and login all served same-origin through Vite with no CORS headers at all, and SSE **streamed incrementally over 4.6 s** rather than arriving as one buffered chunk. |

### What changed from the plan

1. **§2.16 is new** — a real bug in the path this change depends on, found by testing the Stop button
   rather than assuming it worked.
2. **Aborts read the status back instead of guessing.** The plan had the client infer `incomplete`
   from whether it had text. That is wrong in the one case that matters: stop a *summary* before its
   first field lands and the client would say `complete` while the server recorded `error`. The
   client now adopts the stored status, which also picks up FR-24's marking. This is what
   `GET /api/turns/{id}` is for, earlier than the plan parked it.
3. **The poll drives upload rows too.** The plan had it refresh `documents`; the upload list needs the
   same tick to move its progress bar, so one poll updates both.
4. **`validateFile` takes its limits as an argument** rather than reading a store. One less piece of
   hidden state, and the store passes `useConfig`'s copy.
5. **Search went in with paging.** `?q=` was I8's, but the sidebar's existing search box would
   otherwise have silently searched only the first page. It debounces to the server at 250 ms while
   the local filter keeps typing instant.
6. **`UsageLedger` surfaced the budget too.** The settings page now measures against the server's
   `budgetPerAskUsd` rather than a hard-coded `0.05`.

### Found, not fixed — flagged deliberately

**Concurrent sends into one conversation 500.** 25 parallel turns against the *same* conversation
produced 16 `UNIQUE constraint failed: messages.conversation_id, messages.seq`. `next_seq` reads
`max(seq)+1` and then inserts, with no guard between. It is pre-existing, out of this pass's scope,
and unreachable from the UI — the composer disables send while a turn streams, and the race needs
concurrent sends into the *same* thread (different conversations are unaffected). It wants a retry on
the integrity error or a per-conversation counter, and it should be fixed before anything can drive
the API concurrently.
