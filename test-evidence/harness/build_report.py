"""Build the Legal Lens E2E test report workbook, with screenshots embedded."""

from __future__ import annotations

import json
import pathlib

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from PIL import Image as PILImage

EV = pathlib.Path("/Users/sekhar/Desktop/Batch B7/LegalLens/test-evidence")
SHOTS = EV / "screenshots"
THUMBS = EV / ".thumbs"
OUT = EV / "LegalLens-E2E-Test-Report.xlsx"
THUMBS.mkdir(exist_ok=True)

FONT = "Arial"
NAVY = "1F3A5F"
GREY = "F2F2F2"
PASS_FILL = PatternFill("solid", fgColor="D9EAD3")
FAIL_FILL = PatternFill("solid", fgColor="F4CCCC")
HDR_FILL = PatternFill("solid", fgColor=NAVY)
SUB_FILL = PatternFill("solid", fgColor=GREY)
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

rows = [json.loads(l) for l in (EV / "results.jsonl").read_text().splitlines() if l.strip()]
rows.sort(key=lambda r: int(r["id"].split("-")[1]))


def base(cell, *, bold=False, size=10, wrap=False, valign="top", halign="left"):
    cell.font = Font(name=FONT, bold=bold, size=size)
    cell.alignment = Alignment(wrap_text=wrap, vertical=valign, horizontal=halign)
    cell.border = BOX
    return cell


def header_row(ws, labels, widths, row=1):
    for i, (label, width) in enumerate(zip(labels, widths), start=1):
        c = ws.cell(row=row, column=i, value=label)
        c.font = Font(name=FONT, bold=True, size=10, color="FFFFFF")
        c.fill = HDR_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="left")
        c.border = BOX
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[row].height = 30
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def title_block(ws, title, subtitle, span):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=span)
    t = ws.cell(row=1, column=1, value=title)
    t.font = Font(name=FONT, bold=True, size=14, color="FFFFFF")
    t.fill = HDR_FILL
    t.alignment = Alignment(vertical="center", horizontal="left", indent=1)
    ws.row_dimensions[1].height = 26

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=span)
    s = ws.cell(row=2, column=1, value=subtitle)
    s.font = Font(name=FONT, size=9, italic=True)
    s.fill = SUB_FILL
    s.alignment = Alignment(vertical="center", horizontal="left", indent=1)
    ws.row_dimensions[2].height = 20


wb = Workbook()

# ----------------------------------------------------------------- Summary
ws = wb.active
ws.title = "Summary"
title_block(
    ws,
    "Legal Lens — end-to-end functional test report",
    "Playwright against the live UI (localhost:5173) and the live backend (127.0.0.1:8000), "
    "LL_AI_ENGINE=auto resolving to orchestrated/m4 — real bge-m3 retrieval, real Claude Sonnet 5 generation.",
    6,
)

meta = [
    ("Executed", "2026-10-13 / 2026-10-14 (local)"),
    ("Build under test", "branch feature/webcode, commit 9ee3e46 (+ one fix applied during the run — see TC-60)"),
    ("Frontend", "React 19 · Vite dev server · http://localhost:5173"),
    ("Backend", "FastAPI · uvicorn · http://127.0.0.1:8000"),
    ("Generation engine", "orchestrated/m4 · claude-sonnet-5 · prompt ask/v1.0.0+f95069f0"),
    ("Intent router", "claude-haiku-4-5 · temperature 0.0"),
    ("Retrieval", "BAAI/bge-m3 dense+sparse → Qdrant (local, data/qdrant) → RRF → BAAI/bge-reranker-v2-m3, CPU"),
    ("Grounded", "true (GET /readyz → generation.grounded = true) — no fixture answers in this run"),
    ("Test method", "Functional, black-box through the browser; every UI assertion cross-checked against the REST API"),
    ("Documents used", "3 uploaded through the real ingestion pipeline + 9 pre-seeded fixture documents"),
]
r = 4
for k, v in meta:
    base(ws.cell(row=r, column=1, value=k), bold=True)
    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)
    base(ws.cell(row=r, column=2, value=v), wrap=True)
    ws.row_dimensions[r].height = 15
    r += 1

ws.column_dimensions["A"].width = 22
for col in "BCDEF":
    ws.column_dimensions[col].width = 22

n_total = len(rows)
n_pass = sum(1 for x in rows if x["status"] == "PASS")
n_fail = sum(1 for x in rows if x["status"] == "FAIL")

r += 1
base(ws.cell(row=r, column=1, value="RESULTS"), bold=True, size=11)
r += 1
header_row(ws, ["", "Live formula", "Verified at run time", "", "", ""], [22, 22, 22, 22, 22, 22], row=r)
r += 1
res_start = r
for label, formula, verified in [
    ("Total test cases", "=COUNTA('Test Cases'!A4:A1000)", n_total),
    ("Passed", "=COUNTIF('Test Cases'!F4:F1000,\"PASS\")", n_pass),
    ("Failed", "=COUNTIF('Test Cases'!F4:F1000,\"FAIL\")", n_fail),
    ("Pass rate", "=IFERROR(B{p}/B{t},0)", n_pass / n_total),
]:
    base(ws.cell(row=r, column=1, value=label), bold=True)
    f = formula.format(p=res_start + 1, t=res_start)
    c = base(ws.cell(row=r, column=2, value=f), halign="right")
    v = base(ws.cell(row=r, column=3, value=verified), halign="right")
    if label == "Pass rate":
        c.number_format = "0.0%"
        v.number_format = "0.0%"
    r += 1

base(
    ws.cell(
        row=r,
        column=1,
        value="Column B recalculates from the Test Cases sheet when opened in Excel. Column C is the count "
        "observed by the harness at run time, recorded so the figures are readable without recalculation; "
        "the two agreed when this file was written.",
    ),
    wrap=True,
).font = Font(name=FONT, size=8, italic=True)
ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
ws.row_dimensions[r].height = 26
r += 2

base(ws.cell(row=r, column=1, value="BY SCENARIO"), bold=True, size=11)
r += 1
header_row(
    ws,
    ["Scenario", "Cases", "Passed", "Failed", "Verified cases", "Verified failed"],
    [34, 11, 11, 11, 14, 15],
    row=r,
)
r += 1
for scenario in sorted({x["scenario"] for x in rows}):
    cases = sum(1 for x in rows if x["scenario"] == scenario)
    failed = sum(1 for x in rows if x["scenario"] == scenario and x["status"] == "FAIL")
    base(ws.cell(row=r, column=1, value=scenario), wrap=True)
    base(ws.cell(row=r, column=2, value=f'=COUNTIF(\'Test Cases\'!B4:B1000,A{r})'), halign="right")
    base(ws.cell(row=r, column=3, value=f'=COUNTIFS(\'Test Cases\'!B4:B1000,A{r},\'Test Cases\'!F4:F1000,"PASS")'), halign="right")
    base(ws.cell(row=r, column=4, value=f'=COUNTIFS(\'Test Cases\'!B4:B1000,A{r},\'Test Cases\'!F4:F1000,"FAIL")'), halign="right")
    base(ws.cell(row=r, column=5, value=cases), halign="right")
    fc = base(ws.cell(row=r, column=6, value=failed), halign="right")
    if failed:
        fc.fill = FAIL_FILL
    r += 1

r += 1
ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
note = base(
    ws.cell(
        row=r,
        column=1,
        value="The three requested workflows all work. Every failure below is a backend defect or a measured "
        "budget overrun, not a broken screen — see the Defects sheet for root cause and the Evidence sheet "
        "for the screenshot behind each case.",
    ),
    wrap=True,
)
note.font = Font(name=FONT, size=9, italic=True)
ws.row_dimensions[r].height = 42

# -------------------------------------------------------------- Test Cases
tc = wb.create_sheet("Test Cases")
title_block(
    tc,
    "Test cases — executed functionally through the UI",
    "Expected is what the requirement says. Actual is what was observed, including the server-side record "
    "each UI assertion was checked against.",
    8,
)
header_row(
    tc,
    ["ID", "Scenario", "Test case", "Expected result", "Actual result (observed)", "Status", "Secs", "Evidence"],
    [9, 26, 44, 46, 76, 9, 7, 42],
    row=3,
)
r = 4
for x in rows:
    base(tc.cell(row=r, column=1, value=x["id"]), bold=True)
    base(tc.cell(row=r, column=2, value=x["scenario"]), wrap=True)
    base(tc.cell(row=r, column=3, value=x["title"]), wrap=True)
    base(tc.cell(row=r, column=4, value=x["expected"]), wrap=True)
    base(tc.cell(row=r, column=5, value=x["actual"]), wrap=True)
    sc = base(tc.cell(row=r, column=6, value=x["status"]), bold=True, halign="center")
    sc.fill = PASS_FILL if x["status"] == "PASS" else FAIL_FILL
    base(tc.cell(row=r, column=7, value=x["seconds"] or None), halign="right")
    ev = list(x.get("shots", [])) + [f"logs/{n}" for n in x.get("logs", [])]
    base(tc.cell(row=r, column=8, value="\n".join(ev) if ev else "—"), wrap=True, size=8)
    tc.row_dimensions[r].height = max(30, 11 * min(9, 1 + len(x["actual"]) // 72))
    r += 1
tc.auto_filter.ref = f"A3:H{r - 1}"

# ----------------------------------------------------------------- Defects
df = wb.create_sheet("Defects")
title_block(
    df,
    "Defects found",
    "Severity 1 = data loss or service outage. Severity 2 = requirement not met. "
    "Severity 3 = quality gap worth a decision.",
    7,
)
header_row(
    df,
    ["Ref", "Severity", "Area", "Defect", "Root cause (file:line)", "Impact", "Suggested fix"],
    [9, 11, 20, 46, 60, 48, 48],
    row=3,
)
defects = [
    (
        "TC-60", "1 — FIXED", "AI orchestration",
        "Every Ask / Summary / Draft turn hung forever on a fresh process, and the whole backend stopped "
        "serving requests while still accepting TCP connections.",
        "app/ai/orchestrated_engine.py:75 created a non-reentrant threading.Lock(). _ask_workflow() took it at "
        "line 143 then called _retriever() at line 148, which takes the same lock at line 116 → self-deadlock on "
        "the asyncio event-loop thread. _summary_workflow() (156→161) and _draft_workflow() (167→171) had the "
        "same shape.",
        "100% reproducible. No turn of any kind could complete through the API; GET /readyz timed out. "
        "The product's entire value proposition was unreachable.",
        "FIXED — orchestrated_engine.py:78 now uses threading.RLock(). Verified: the same turn returns a "
        "grounded, cited answer in 58s, and all three workflows pass end to end.",
    ),
    (
        "TC-59", "1 — FIXED", "Retrieval / stability",
        "The backend segfaulted and died when two turns' retrievals overlapped. Reproduced with three "
        "SEQUENTIAL POST /api/summarize calls.",
        "app/ai/orchestration/retrieval/factory.py caches one bge-m3 embedder and one bge-reranker instance per "
        "process; its _lock guarded construction only, not inference. summary.py:448 and draft.py:206 dispatch "
        "retrieve() via asyncio.to_thread, so two worker threads entered the same torch modules concurrently — "
        "crash stack showed m3.encode_single_device (pipeline.py:115) and the cross-encoder's sdpa_mask "
        "(rerank.py:107) live at the same instant.",
        "Whole process died; every in-flight stream lost; manual restart required. Reachable in ordinary "
        "sequential use because an abandoned turn left its to_thread retrieval running into the next turn.",
        "FIXED — new app/ai/orchestration/retrieval/inference.py holds one process-wide INFERENCE_LOCK, taken at "
        "the leaf torch calls in embedder.embed_documents / embed_query and rerank._score, shared between BOTH "
        "models, so two simultaneous forward passes are impossible. Verified: 3 sequential summaries run "
        "80/74/78s with all 7 fields, and 4 OVERLAPPING turns all complete with 0 segfaults; 517 tests, ruff "
        "and mypy --strict pass. Caveat: a parallel change by another contributor added startup model warm-up "
        "between the before and after runs, so the wall-clock improvement is not attributable to this lock "
        "alone. Serialising inference is the honest trade until it moves to a GPU or a dedicated process.",
    ),
    (
        "TC-62", "2 — major", "Persistence / concurrency",
        "With several turns in flight, one fails with 500 Internal Server Error — "
        "sqlite3.OperationalError: database is locked. Intermittent (1 of 4 in the observed run).",
        "NOT a missing pragma: app/db.py already sets journal_mode=WAL, busy_timeout=30000, "
        "synchronous=NORMAL and a 30s connect timeout. This is writer-vs-writer contention — a turn holds its "
        "write transaction across 50–120s of retrieval and generation, and a second writer upgrading a deferred "
        "read transaction gets SQLITE_BUSY immediately rather than waiting out busy_timeout.",
        "Blocks NFR-5's ten concurrent users. Previously invisible because the process segfaulted (TC-59) "
        "before this could ever surface.",
        "Structural, not a one-liner: keep write transactions short and off the streaming path (commit the turn "
        "record before generation, update after), or bring forward the M6 move to Postgres.",
    ),
    (
        "TC-58", "2 — major", "Turn lifecycle",
        "The 60-second generation budget fires at unpredictable times: one summary ran 237.6s and was recorded "
        "complete, another was killed at 129s and recorded incomplete after 2 of 7 fields.",
        "app/api/routes/turns.py:250 wraps an async generator in `async with asyncio.timeout(...)` spanning many "
        "yields. asyncio.timeout cancels the task driving __anext__ at the moment the deadline passes, so which "
        "turn dies — and whether it dies at all — depends on where the event loop happens to be.",
        "Identical requests produce different outcomes. A user can get a silently truncated summary presented as "
        "a finished one in the same session where a slower summary succeeded.",
        "Enforce the deadline inside the workflow between stages (check a monotonic deadline before each field / "
        "section), or wrap the consumer rather than the generator. Also raise the budget: no real summary fits "
        "in 60s on this hardware.",
    ),
    (
        "TC-39", "2 — major", "Performance (NFR-3)",
        "NFR-3 requires a 200-page summary in ≤ 3 min. Post-fix, a 3-page 9-field contract summary measured "
        "178.0s / 142.5s / 187.2s (median 178.0s) — straddling the 180s line on a document 1.5% of the target size.",
        "The 9 template fields are retrieved strictly sequentially (summary.py:283 `for template_field in "
        "fields[:budget]`), each paying a full bge-m3 embed plus a 25-candidate cross-encoder rerank on CPU. "
        "Field retrieval is 87–129s of each run.",
        "Improved by the TC-59 fix (was 237.6s) but not reliably inside budget even for a tiny document, and the "
        "200-page case the requirement actually specifies remains unproven.",
        "Run the field retrievals concurrently now that inference is thread-safe (a bounded gather behind the "
        "INFERENCE_LOCK still serialises torch, so the real win is batching the rerank across fields or moving "
        "the cross-encoder to GPU / a hosted reranker).",
    ),
    (
        "TC-40", "2 — major", "Performance (NFR-1)",
        "NFR-1 requires p95 complete answer ≤ 15s. 13 pre-fix Ask turns: median 29.4s, slowest 142.6s. "
        "3 post-fix turns: 32.6s, 32.9s, 29.6s. 0 of 16 within budget.",
        "Cross-encoder reranking dominates every turn. Separately, ask.py:168 still calls retriever.retrieve() "
        "synchronously on the event loop — unlike summary.py and draft.py, which correctly use asyncio.to_thread "
        "— so an Ask turn also blocks every other request for its whole retrieval.",
        "Answers are 2–10x over the latency budget, and during an Ask turn the server serves nothing else, "
        "which also works against NFR-5.",
        "One-line parity fix first: wrap ask.py's retrieve() in asyncio.to_thread like the other two workflows. "
        "Then reduce rerank cost (GPU, smaller candidate_k, or a hosted reranker).",
    ),
    (
        "TC-33", "3 — quality", "Retrieval recall",
        "A natural paraphrase is declined while a near-verbatim question about the same clause is answered. "
        "\"What is the liability cap and what carve-outs apply to it?\" scored best relevance 0.0436 against a "
        "0.25 floor; \"What is the cap on each party's total aggregate liability?\" cited § 6.1 correctly.",
        "Single-vector bge-m3 similarity plus a 0.25 rerank floor, with no query expansion on a first turn: "
        "rewriter.py short-circuits to source=\"no_history\" when there is no prior turn, so a paraphrase gets "
        "no help. The document says \"Exceptions to the Cap\", never \"carve-outs\".",
        "The refusal is honest and auditable — it names the query it ran and suggests using the document's own "
        "wording — but an attorney using ordinary professional shorthand is told the answer is not in a document "
        "that plainly contains it. A false refusal, counted against the ≥90% correct-refusal target.",
        "Run the rewriter (or a cheap synonym expansion) on first turns too, and consider a lower floor with "
        "more aggressive gating downstream. Worth a labelled paraphrase set to measure recall before tuning.",
    ),
]
r = 4
for ref, sev, area, defect, cause, impact, fix in defects:
    base(df.cell(row=r, column=1, value=ref), bold=True)
    sc = base(df.cell(row=r, column=2, value=sev), bold=True, wrap=True)
    sc.fill = PASS_FILL if "FIXED" in sev else (FAIL_FILL if sev.startswith("1") else SUB_FILL)
    base(df.cell(row=r, column=3, value=area), wrap=True)
    base(df.cell(row=r, column=4, value=defect), wrap=True)
    base(df.cell(row=r, column=5, value=cause), wrap=True)
    base(df.cell(row=r, column=6, value=impact), wrap=True)
    base(df.cell(row=r, column=7, value=fix), wrap=True)
    df.row_dimensions[r].height = 118
    r += 1

# ---------------------------------------------------------------- Evidence
evs = wb.create_sheet("Evidence")
title_block(
    evs,
    "Screenshot evidence",
    "Captured by Playwright at the moment each assertion was evaluated. Grouped by test case, in test order.",
    4,
)
evs.column_dimensions["A"].width = 13
evs.column_dimensions["B"].width = 30
evs.column_dimensions["C"].width = 120
evs.column_dimensions["D"].width = 40

MAX_W = 820
r = 4
by_id = {x["id"]: x for x in rows}
for x in rows:
    shots = x.get("shots", [])
    logs = x.get("logs", [])
    if not shots and not logs:
        continue

    evs.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
    h = evs.cell(row=r, column=1, value=f"{x['id']} — {x['title']}   [{x['status']}]")
    h.font = Font(name=FONT, bold=True, size=11, color="FFFFFF")
    h.fill = HDR_FILL
    h.alignment = Alignment(vertical="center", indent=1)
    evs.row_dimensions[r].height = 22
    r += 1

    evs.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
    a = evs.cell(row=r, column=1, value=f"Observed: {x['actual']}")
    a.font = Font(name=FONT, size=9, italic=True)
    a.fill = SUB_FILL
    a.alignment = Alignment(wrap_text=True, vertical="top", indent=1)
    evs.row_dimensions[r].height = max(24, 11 * (1 + len(x["actual"]) // 150))
    r += 1

    for name in logs:
        c = evs.cell(row=r, column=1, value=f"log: logs/{name}")
        c.font = Font(name=FONT, size=9, bold=True, color="0563C1")
        r += 1

    for name in shots:
        src = SHOTS / name
        if not src.exists():
            continue
        c = evs.cell(row=r, column=1, value=name)
        c.font = Font(name=FONT, size=8)
        c.alignment = Alignment(vertical="top", wrap_text=True)

        with PILImage.open(src) as im:
            im = im.convert("RGB")
            w, hgt = im.size
            if w > MAX_W:
                hgt = int(hgt * MAX_W / w)
                w = MAX_W
                im = im.resize((w, hgt), PILImage.LANCZOS)
            thumb = THUMBS / name.replace(".png", ".jpg")
            im.save(thumb, "JPEG", quality=82)

        img = XLImage(str(thumb))
        img.width, img.height = w, hgt
        evs.add_image(img, f"B{r}")
        rows_needed = max(1, int(hgt / 19) + 1)
        for i in range(rows_needed):
            evs.row_dimensions[r + i].height = 19
        r += rows_needed + 1
    r += 1

# ------------------------------------------------------------- Environment
en = wb.create_sheet("Environment")
title_block(en, "Environment and test data", "What was running, and what the tests ran against.", 3)
header_row(en, ["Item", "Value", "Notes"], [30, 62, 70], row=3)
env = [
    ("Platform", "macOS (Darwin 24.6.0), Apple silicon, CPU-only inference", "No GPU; this is the dominant factor in every latency finding"),
    ("Backend process", "uvicorn app.start:app on 127.0.0.1:8000", "Launched with a SIGUSR1 faulthandler hook to diagnose the hangs"),
    ("/readyz database", "ok", ""),
    ("/readyz ingestion worker", "running (3/3 workers)", ""),
    ("/readyz OCR", "ocr/none — pytesseract and rapidocr both absent", "Scanned PDFs are quarantined, not indexed. Not exercised; TXT fixtures used"),
    ("/readyz indexer", "indexer/bge-m3+qdrant, vectors available", ""),
    ("/readyz generation", "orchestrated/m4, grounded=true", "Confirms no fixture answers were served"),
    ("Upload limits (GET /api/config)", "50 MB · 500 pages · 50 documents · .pdf .docx .txt", "Asserted against the dialog copy in TC-10"),
    ("Generation budget", "60s (generationBudgetS)", "See TC-58"),
    ("Seeded fixture corpus", "9 documents incl. a 312-page MSA, 2 opinions, a pleading", "Has chunk_meta rows but NO vectors in Qdrant — see note below"),
    ("Documents uploaded by these tests", "Orion-Vertex-Services-Agreement.txt (contract, 3 pp, 27 chunks / 54 points)", "Scenario 1 Ask + Scenario 2a Summary + Scenario 3 Draft source"),
    ("", "Calder-Ridgeway-Supply-Agreement.txt (contract, uploaded through the UI in TC-12)", "Proves the upload path end to end"),
    ("", "Thalberg-v-Winslow-Freight.txt (opinion, 2 pp)", "Scenario 2b upload-then-summarise"),
    ("Rejection fixture", "unsupported-brief.pptx", "TC-11 per-file rejection"),
    ("Test harness", "Playwright 1.64.0 (Chromium, headless, 1600x1000)", "60 cases, 81 screenshots"),
    ("Spend observed", "Ask $0.0075 · Summary $0.066 · Draft $0.065 per turn", "NFR-8 caps Ask at $0.05 — Ask is within budget"),
]
r = 4
for k, v, n in env:
    base(en.cell(row=r, column=1, value=k), bold=bool(k))
    base(en.cell(row=r, column=2, value=v), wrap=True)
    base(en.cell(row=r, column=3, value=n), wrap=True)
    en.row_dimensions[r].height = 28
    r += 1

r += 1
en.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
w = base(
    en.cell(
        row=r,
        column=1,
        value="IMPORTANT — the seeded demo corpus is not retrievable by the real engine. `make seed` writes "
        "documents, pages and chunk_meta rows marked `ready`, but no vectors: Qdrant held only two unrelated "
        "ad-hoc files at the start of this run. So the README's showcase question — \"What happens on a change "
        "of control?\" against the 312-page MSA, then clicking citation [1] through to page 84 — refuses under "
        "LL_AI_ENGINE=auto, correctly but unhelpfully (best relevance 0.00 vs a 0.25 floor). Every grounded "
        "result in this report therefore came from documents these tests uploaded. Fix by having `make seed` "
        "run the real indexer, or by shipping a pre-built Qdrant collection with the fixtures.",
    ),
    wrap=True,
)
w.fill = FAIL_FILL
en.row_dimensions[r].height = 96

wb.save(OUT)
print(f"wrote {OUT}")
print(f"  sheets: {wb.sheetnames}")
print(f"  cases: {len(rows)}  embedded images: {len(list(THUMBS.glob('*.jpg')))}")
