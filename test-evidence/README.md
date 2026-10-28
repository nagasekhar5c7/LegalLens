# Legal Lens — end-to-end functional test evidence

Executed 2026-10-13 / 14 against the live UI and the live backend, with
`LL_AI_ENGINE=auto` resolving to `orchestrated/m4` — real bge-m3 + Qdrant retrieval and real
Claude Sonnet 5 generation. `GET /readyz` reported `generation.grounded = true` throughout, so
nothing in here was answered by the fixture engine.

**62 test cases · 56 passed · 6 failed.** Both severity-1 defects are fixed and verified.

## What's here

| Path | What it is |
|---|---|
| `LegalLens-E2E-Test-Report.xlsx` | The report. Five sheets: Summary, Test Cases, Defects, Evidence (81 embedded screenshots), Environment |
| `screenshots/` | The same 81 PNGs at full resolution, named `TC-<case>-<seq>-<what>.png` |
| `logs/` | Crash dumps and SSE captures behind the three backend defects |
| `harness/` | The Playwright suite, so the run can be repeated |
| `testdocs/` | The documents the tests uploaded |
| `results.jsonl` | One JSON row per case — the raw record the workbook is built from |

## The three requested workflows

All three work, with real citations:

- **Ask** — `TC-21`…`TC-29`. A question about an indexed contract returned a grounded answer
  citing `§ 4.3` (page 2) and `§ 4.2` (page 1), each chip resolving to a real span, each hover
  card showing the retrieved text, and `Open document` landing on the cited page.
- **Summary** — `TC-34`…`TC-39` (existing document) and `TC-41`…`TC-44` (upload, then
  summarise). Per-field retrieval against the detected template, absent terms reported as
  absent, honest page-coverage note.
- **Draft** — `TC-45`…`TC-49`. A memo in the required five-section structure, marked
  `UNREVIEWED AI DRAFT`, citing only the sources selected in the drawer.

Conversation create / rename / delete are `TC-21`, `TC-30`, `TC-31`.

## Reproducing

```bash
# backend
cd backend && uv run uvicorn app.start:app --host 127.0.0.1 --port 8000

# frontend
cd frontend && npm run dev

# suite — run the phases in order, sequentially, never in parallel
cd test-evidence/harness
node phase1.mjs   # auth, shell, library, upload, composer           (fast)
node phase2.mjs   # scenario 1: create / ask / citations / rename / delete
node phase2b.mjs  # citation resolution, refusal, retrieval recall
node phase3.mjs   # scenario 2a: summarise an existing document
node phase3b.mjs  # latency measurement
node phase4.mjs   # scenario 2b: upload a new document
node phase7.mjs   # ... then summarise it
node phase5.mjs   # scenario 3: draft a memo
node phase6.mjs   # palette, panels, pin, export, feedback, stop, sign out
node phase8.mjs   # concurrency regression for the TC-59 segfault fix

# rebuild the workbook from results.jsonl
uv run --with openpyxl --with pillow python build_report.py
```

Run the phases **one at a time**. `phase8.mjs` deliberately overlaps turns — that is the
regression test for `TC-59`, and it now passes.

## Two things to know before reading the results

**The seeded demo corpus has no vectors.** `make seed` writes `documents`, `document_pages` and
`chunk_meta` rows marked `ready`, but never embeds them, so the real engine cannot retrieve any
of them. The README's showcase — "What happens on a change of control?" against the 312-page
MSA, then clicking `[1]` through to page 84 — refuses under `LL_AI_ENGINE=auto`, correctly
(best relevance 0.00 against a 0.25 floor) but unhelpfully. Every grounded result in this
report came from documents the tests uploaded themselves.

**Two severity-1 defects were found by these tests and fixed.**

- `TC-60` — every Ask, Summary and Draft turn deadlocked on the first request and took the
  whole backend down with it. `backend/app/ai/orchestrated_engine.py:78` now uses
  `threading.RLock()` instead of `threading.Lock()`.
- `TC-59` — the process segfaulted when two retrievals overlapped. New
  `backend/app/ai/orchestration/retrieval/inference.py` holds one process-wide
  `INFERENCE_LOCK`, taken at the leaf torch calls in `embedder.py` and `rerank.py` and shared
  between both models, so two simultaneous forward passes are impossible. Verified with 3
  sequential summaries (80/74/78s, all fields) and 4 overlapping turns (all complete, 0
  segfaults).

Those three files are the only product code this work changed. `ruff`, `mypy --strict` and
all 517 backend tests pass.

**Attribution caveat.** Another contributor was editing `backend/` in parallel during this
work — including background model warm-up at startup (`start.py`, `factory.warm_models`), a
`relevance_floor` override in `pipeline.py`, and changes to `settings.py`, `summary.py`,
`turns.py`, `conversations.py`. The before/after wall-clock numbers therefore span more than
the lock, and the speedup is not attributable to it alone. A controlled A/B with the lock
disabled was started and then abandoned rather than leave a knowingly crash-prone backend
running while the environment was in active use.

## Failures, in severity order

| Ref | Severity | What |
|---|---|---|
| `TC-60` | 1 — **fixed** | Non-reentrant lock deadlocked every turn and wedged the server |
| `TC-59` | 1 — **fixed** | Segfault when two retrievals overlap — shared bge-m3 / reranker were not inference-safe |
| `TC-62` | 2 | `database is locked` 500 under concurrent turns. Was masked by `TC-59` crashing first |
| `TC-58` | 2 | The 60s generation budget fires unpredictably: 237.6s summary completed, 129s summary truncated |
| `TC-39` | 2 | NFR-3 (200-page summary ≤ 3 min): 3-page summary now 142–187s, straddling the line |
| `TC-40` | 2 | NFR-1 (complete answer ≤ 15s): median 32.6s post-fix, 0 of 16 turns within budget |
| `TC-33` | 3 | A natural paraphrase is refused where a near-verbatim question succeeds on the same clause |

The one-line follow-up worth doing next: `ask.py:168` still calls `retriever.retrieve()`
synchronously on the event loop, unlike `summary.py` and `draft.py`. Wrapping it in
`asyncio.to_thread` gives parity and stops an Ask turn blocking every other request.

Root cause with file and line for each is on the **Defects** sheet.
