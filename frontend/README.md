# Legal Lens — Frontend

The UI layer, wired to the real API. Every screen reads from the FastAPI backend in `../backend`:
real session auth, real uploads, real page text, real SSE streaming, real deletion. Built against
the requirement codes in `../docs/LegalLens-BRD-PRD-FRD.md` and the frontend lane of
`../docs/architecture/legallens-technical-flow.excalidraw`.

## Run it

These commands work the same on macOS, Linux and Windows (PowerShell or Command Prompt).

Start the backend first, in its own terminal (see `../backend/README.md`):

```
cd backend
uv sync --extra dev --extra ai
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.start:app --reload --host 127.0.0.1 --port 8000
```

Then, in a second terminal:

```
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 and sign in with `dana@example.com` / `counsel-demo-2026`.

Or run both with `docker compose up --build` from the repo root.

### How it reaches the API

`vite.config.ts` proxies `/api`, `/healthz` and `/readyz` to `http://127.0.0.1:8000`, so the app is
same-origin in development and the session cookie needs no thought. Point it elsewhere with
`LL_BACKEND_ORIGIN`.

Set `VITE_API_BASE_URL` to an absolute origin to talk to a backend cross-origin instead — which is
what `docker-compose.yml` does. That path works, but note the cookie is `SameSite=Lax`: it survives
`localhost:5173 → localhost:8000` because a port is not part of a "site", and it does **not** survive
`127.0.0.1:5173 → localhost:8000`, which presents as a successful login followed by 401s on
everything. Prefer the proxy locally.

## Where things are

```
src/
  api/        types.ts = the wire contract · client.ts = THE SEAM
              http.ts = fetch, credentials, ApiError, the 401 path
              sse.ts  = POST + ReadableStream → AsyncGenerator<StreamEvent>
              env.ts  = base URL resolution
  store/      zustand: session · config · ui · workspace · conversations · composer · evidence · toasts
  hooks/      usePages = the viewer's page cache, with abort-on-scroll-past
  lib/        templates (summary/draft skeletons) · scenarios · format · hotkeys · exporting
  dev/        fixtures.ts — sample data for /kitchen-sink, and nothing else
  components/ ui · auth · shell · sidebar · composer · chat · answer · evidence · documents · viewer · trace
  pages/      Chat · Library · Document · Settings · Login · KitchenSink · NotFound
  styles/     theme.css = every design token · global.css = reset, focus, keyframes
```

Routes: `/` · `/c/:conversationId` · `/library` · `/doc/:documentId?page=&span=` · `/settings` ·
`/login` · `/kitchen-sink`

`/doc/:id?page=84&span=s_msa_1223` is a first-class URL: it is how an attorney sends a colleague the
exact clause.

## The API seam

**`src/api/client.ts` is the only module that knows a URL.** Stores call `api.*`; no component calls
`fetch`. Two modules sit under it so credentials, error parsing and the 401 path are decided once:

- **`http.ts`** — `credentials: 'include'`, any 2xx, 204 → `undefined`, and every non-2xx parsed into
  the `ApiError` the backend sends as its error body. A 401 flips the session to signed-out once
  rather than raising a toast per in-flight call.
- **`sse.ts`** — `POST` plus a `ReadableStream` line parser, because `EventSource` is GET-only and the
  request body carries `scope`, `mode` and `draftParams`. It buffers across chunk boundaries, skips
  the keep-alive comments that let a three-minute summary survive an idle-connection reaper, and
  turns a 429 or 401 arriving where a stream was expected into one `error` event.

`sendMessage` returns an `AsyncGenerator<StreamEvent>` over the fifteen event variants in
`types.ts`. The server's persisted message ids come back as `X-LegalLens-Turn-Id` /
`X-LegalLens-User-Turn-Id` headers before the first token: the UI renders optimistically with ids of
its own, and adopts the server's so feedback attaches to the turn the user is looking at.

Stop aborts the fetch. The server notices the disconnect, records the turn as `incomplete` with the
partial text kept, and the client reads that status back rather than guessing — NFR-7 is about what
the record says, not only what the screen paints.

## Reviewing the UI

Two things to use:

- **The Demo menu** (top bar) forces the next response down a chosen path: `refusal · llm_down ·
  rate_limited · timeout · interrupted · slow`. It is not a simulation — the id travels as
  `X-LegalLens-Scenario` and the backend takes that path for real, so the card on screen is a genuine
  response. The menu hides itself when the server reports `scenariosEnabled: false`.
- **`/kitchen-sink`** shows every primitive and every semantic state side by side — all four
  semantic colours, every error variant, every document status.

The seeded corpus is deliberately not all healthy: a 312-page MSA, a scanned amendment with two
low-OCR-confidence pages, one document mid-indexing, one hard parse failure. Those three exercise
FR-3, FR-7 and DR-1.

**Asking something the corpus does not cover genuinely refuses.** The refusal path is the fallback,
not a special case — try "what does the MSA say about force majeure?".

## Design system

Tokens live in `src/styles/theme.css` and nowhere else. Components use Tailwind utilities; there is
no parallel stylesheet to drift out of sync, and answer typography lives in the markdown renderer
rather than a global `.prose` class.

Colour carries meaning, never decoration:

| Meaning | Token |
|---|---|
| Primary | `navy-600` `#2B5080` |
| Grounded / verified | `grounded` `#2F6B4F` |
| Refusal — a success state, deliberately not alarming | `refusal` `#55606B` |
| Unreviewed draft | `draft` `#8A5A12` |
| Error | `danger` `#9B2C2C` |
| OCR / caution | `caution` `#8A6A12` |

Inter for chrome, **Source Serif 4 for answers and document text** (work product should not look
like UI chrome), JetBrains Mono for span ids and trace values. Light theme only; tokens are authored
so a dark theme is one override block.

## Toolchain note

TypeScript is pinned to **6.x**: `typescript-eslint` hard-blocks TS 7.0 at runtime
([typescript-eslint#10940](https://github.com/typescript-eslint/typescript-eslint/issues/10940)).
Working `react-hooks/exhaustive-deps` was judged worth more than the newer compiler. Unpin once that
issue closes. Peer installs use `--legacy-peer-deps` for the same reason.

## Not here yet

**Page images.** `GET /api/documents/{id}/pages/{n}/image` answers 404 by design until the backend
grows a rasteriser. The viewer renders page *text* and computes the highlight from character offsets,
so `/doc/doc_msa?page=84&span=s_msa_1223` already lands on the right characters of the right clause —
when PDF.js arrives, the span maths does not change.

`.docx` export (BRD §3) · dark theme (tokens are authored for it; one override block) · multi-user or
roles · compare & redline.
