# Legal Lens

Grounded reading and drafting over an attorney's own documents. Every output traces to a retrieved
span, or the system declines — a refusal is a first-class success state, not an error.

Ask a question across a document set and get an answer where every sentence carries a citation you
can click through to the exact clause on the exact page. Summarise a 312-page agreement against a
schema that reports an absent term as absent rather than inferring it. Draft a memo or a case brief
from sources you chose. Nothing is answered from the model's own knowledge of the law.

## Run it

```bash
cd backend && make install && make seed && make dev   # http://127.0.0.1:8000
cd frontend && npm install && npm run dev             # http://localhost:5173
```

`make seed` prints the dev credentials — `dana@example.com` / `counsel-demo-2026` — and seeds a
workspace that is deliberately not all healthy: a 312-page MSA, two opinions, a pleading, a scanned
amendment with low-OCR-confidence pages, one document mid-indexing and one hard parse failure.

Or both at once:

```bash
docker compose up --build        # then open localhost:5173
```

Once signed in, try **"What happens on a change of control?"** against the whole workspace, then
click citation `[1]`. It lands on page 84 of the MSA with the assignment clause highlighted, because
the highlight is computed from the character offsets the retrieval actually returned.

Then ask something the corpus does not cover — "what does the MSA say about force majeure?" — and
watch it decline, naming what it searched for and why that fell short.

## Layout

```
backend/     FastAPI · Pydantic v2 · SQLAlchemy 2.0 async + SQLite · Alembic · uv
frontend/    React 19 · TypeScript · Vite · Tailwind v4 · zustand · Radix
docs/        BRD/PRD/FRD, glossary, technical architecture
k8s/         AKS manifests — namespace, backend (PVC + ClusterIP), frontend (nginx + LoadBalancer)
.github/     CI on every PR; CD to Azure (ACR → AKS) on merge to main
```

Each has its own README: [backend](backend/README.md) for the API, the data model and what M4
replaces; [frontend](frontend/README.md) for the API seam, the design system and how it reaches the
backend.

The build plans are kept as working documents rather than history:
[BACKEND-TODO.md](BACKEND-TODO.md), [FRONTEND-TODO.md](FRONTEND-TODO.md) and
[INTEGRATION-TODO.md](INTEGRATION-TODO.md).

## Where it stands

Integrated end to end, and the answers are real. Session auth, uploads with real text extraction,
persistence, SSE streaming, hard deletion — and behind `backend/app/ai/ports.py`, a live
orchestration layer: a Haiku intent router, pre-retrieval query rewriting, hybrid retrieval (bge-m3
dense + learned sparse into Qdrant, fused by RRF), a bge-reranker-v2-m3 cross-encoder, child →
parent expansion, generation on Sonnet 5, and the FR-21/FR-22 citation gate that stops a stream
mid-sentence rather than emit a citation it cannot resolve to a source you supplied.

All three workflows are implemented. **Ask** retrieves once and streams token by token. **Summary**
retrieves once *per template field* and reports an absent term as absent rather than inferring it.
**Draft** retrieves once and composes section by section, and withholds the whole document if any
section cites authority the sources do not contain.

The M3 fixture engine is still there, behind the same port, and the two are selected by
`LL_AI_ENGINE`:

```
LL_AI_ENGINE=orchestrated   # the real thing; fails loudly if it cannot run
LL_AI_ENGINE=fixture        # scripted answers, deterministic, offline, free
LL_AI_ENGINE=auto           # orchestrated when it can run, fixture otherwise
```

`auto` is the default and it degrades, because `uv sync` with no extras has to produce a startable
backend. That degradation is *reported*, never silent — `GET /readyz` carries
`generation.grounded`, because a demo answering from scripted fixtures looks exactly like one that
works.

```bash
cd backend && make check      # ruff · mypy --strict · openapi freshness · 510 tests
cd backend && make engine     # which engine would answer a turn, and why
cd frontend && npm run build  # tsc --noEmit + production build
```

The whole AI layer is testable offline — fake retriever, fake chat model, no key, no downloads —
and exercisable live against the real models:

```bash
make install-all                                   # + ai + retrieval (~2.5 GB)
make index src=../docs                             # embed a corpus into Qdrant
make turn q="What happens on a change of control?"  # one turn through the real engine
make turn q="Summarise it" mode=summary
```

## CI and deployment

Every pull request into `main`, and `main` itself, runs
[`.github/workflows/ci.yml`](.github/workflows/ci.yml): the backend's ruff, `mypy --strict`,
OpenAPI-freshness and `pytest` (with coverage) checks, the frontend's ESLint and type-checked
production build, then a build of both Docker images to prove they still assemble. It is the same
surface as `make check` and `npm run build`, run on a clean machine. Nothing is pushed and no secret
is needed — that stays CD's job.

Once CI passes on `main`, [`.github/workflows/cd.yml`](.github/workflows/cd.yml) builds both images,
pushes them to Azure Container Registry tagged with the exact commit SHA, and rolls them out to AKS
from the manifests in [`k8s/`](k8s/). It authenticates by GitHub OIDC, so no Azure password is
stored; deploys run one at a time, and a rollout that never becomes healthy fails the job while the
previous version keeps serving.

The cluster runs one backend pod — SQLite plus an embedded Qdrant own their files exclusively, so
it is `Recreate` over a single 20 GB Azure Disk, never more than one replica — behind a ClusterIP,
and an nginx frontend that serves the built bundle and proxies `/api` to it, exposed on a public
LoadBalancer IP. `LL_BLOB_KEY` and `LL_SECRET_KEY` live in a Kubernetes Secret created once by hand,
never through GitHub. The whole one-time Azure setup — account, resource group, registry, cluster,
that secret, and the GitHub OIDC identity — is written out step by step in
[CD_readme.md](CD_readme.md).
