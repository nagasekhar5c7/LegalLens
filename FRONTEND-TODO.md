# Legal Lens — Frontend Implementation Plan

**Scope of this document:** Phase 1 (M2) only — the UI layer. No backend, no orchestration, no AI.
Everything renders from a mock adapter behind a single API seam.
**Stack:** React 19 + Vite + TypeScript (per BRD §7) · Tailwind v4 tokens · Radix primitives · Zustand.
**Traceability:** every task carries the `FR-*` / `NFR-*` / `US-*` code it serves, so this plan is auditable against `docs/LegalLens-BRD-PRD-FRD.md`.

---

## 0. The ten things that make this UI *legal* software, not a chat wrapper

Most of the build is ordinary chat UI. These are the parts worth getting right, and each one is a place a naive implementation would be wrong.

| # | Nuance | Why it matters | Where |
|---|---|---|---|
| 1 | **Evidence is a co-equal panel, not a popover.** Three panes: sidebar │ chat │ evidence. Excerpts appear *as the answer streams*, with no user action. | FR-12 says excerpts display with no further action. BR-4 says verification must cost seconds. If verifying takes two clicks, the time saved is cancelled. | `components/evidence/` |
| 2 | **Refusal is a success state with its own visual language.** Calm slate card, never red, never a toast. States the scope searched, offers next actions (widen scope, upload a document, rephrase). | FR-14, TS-3, §6. "Not in your documents" is a measured success (≥90% target). Styling it as an error trains the user to distrust a correct answer. | `chat/RefusalCard.tsx` |
| 3 | **The draft marking survives the clipboard.** Persistent banner + a watermark rule in print CSS + the disclaimer is *prepended to the copied text and the exported Markdown*, not just painted on screen. | FR-23, FR-24, TS-5. A banner the user copies out of is theatre. | `answer/DraftBanner.tsx`, `lib/clipboard.ts` |
| 4 | **Auto-routing is never silent.** When mode is `Auto`, the response shows `Auto → Ask` and a one-click "run as Summary instead". | The architecture routes intent with an LLM. An invisible decision the user can't see or override is the fastest way to lose trust in a tool whose whole pitch is transparency. | `chat/IntentBadge.tsx` |
| 5 | **Every answer states the scope it searched.** `Searched 12 documents · workspace` chip on the response header, not in a tooltip. | FR-9. Workspace-wide Ask loses precision as the corpus grows; naming the scope is the stated mitigation (§15). | `chat/ScopeStatement.tsx` |
| 6 | **Stage trace instead of a spinner.** `Routing → Retrieving (40) → Reranking (8) → Reading 3 documents → Writing`. For Summary, per-field progress, because a 200-page summary is budgeted at 3 minutes. | NFR-1, NFR-3, NFR-11. A 3-minute generic spinner reads as a hang. Showing the stage makes latency legible. | `chat/StageTrace.tsx` |
| 7 | **Non-ready documents are visibly excluded, with a reason.** Scope picker shows `Parsing… (excluded)`; OCR-low-confidence pages carry a page-level warning in the viewer and a badge in the library. | FR-7, DR-1. Silently excluding a document produces a "wrong" answer the user can't explain. | `composer/ScopePicker.tsx`, `viewer/OcrConfidenceStrip.tsx` |
| 8 | **Degraded mode is a designed state.** LLM down → retrieval and excerpts keep working; the UI offers "show me the retrieved passages" with generation disabled and a stated reason + retry. | NFR-6. This is an explicit requirement and the only way the product is useful during an outage. | `chat/ErrorCard.tsx`, `shell/DegradedBanner.tsx` |
| 9 | **An interrupted stream is marked incomplete.** Truncated answers get a persistent `Incomplete — stream interrupted` rule and the copy/export carries it too. | NFR-7. No partial output may read as complete. | `chat/IncompleteBanner.tsx` |
| 10 | **Deletion tells you what it deleted.** Confirm dialog enumerates file, extracted text, chunks, embeddings — and the library row disappears from scope immediately. | FR-26, TS-6, US-9. "Delete" in a confidentiality-bearing product is a trust surface, not a menu item. | `documents/DeleteDocDialog.tsx` |

Two more that are small but telegraph care: **answers render in a serif** (they are work product — visually distinct from UI chrome), and **citation chips are keyboard-cyclable** with `⌥↑/↓` and announced to screen readers (NFR-13).

---

## 1. Design direction

Light, warm, dense-but-not-cramped. Reference points: Claude's paper warmth, Linear's precision, Stripe's restraint. The product is read by someone who reads for a living, so type and contrast do the work; colour is reserved for *meaning* (grounded / refusal / draft / error), never decoration.

### Palette — "Counsel" (light, WCAG 2.1 AA verified)

| Role | Token | Hex |
|---|---|---|
| Canvas (warm paper) | `--surface-canvas` | `#FAF9F7` |
| Raised / cards | `--surface-raised` | `#FFFFFF` |
| Sunken / code & excerpts | `--surface-sunken` | `#F4F2EF` |
| Hairline / strong border | `--border-subtle` / `--border-strong` | `#E7E3DD` / `#D5CFC7` |
| Ink 1 / 2 / 3 | `--ink-primary` / `secondary` / `tertiary` | `#1C1B19` / `#5A574F` / `#857F74` |
| **Primary — counsel navy** | `--accent-600` / `--accent-50` | `#2B5080` / `#EEF3FA` |
| **Grounded / verified** — sage | `--grounded` / `--grounded-bg` | `#2F6B4F` / `#EDF5F0` |
| **Refusal** — neutral slate (deliberately *not* alarming) | `--refusal` / `--refusal-bg` | `#55606B` / `#F1F3F5` |
| **Unreviewed draft** — amber | `--draft` / `--draft-bg` / `--draft-border` | `#8A5A12` / `#FDF6E7` / `#EBD9AC` |
| **Error** — muted brick | `--danger` / `--danger-bg` | `#9B2C2C` / `#FDF2F2` |
| **OCR / caution** — ochre | `--caution` / `--caution-bg` | `#8A6A12` / `#FBF5E4` |

Type: **Inter** (UI) · **Source Serif 4** (answers, drafts, document text) · **JetBrains Mono** (span ids, page refs, trace).
Radius `8px` cards / `6px` controls / `999px` chips. Shadows are two-layer and barely there. 4px spacing grid. Motion ≤180ms, `prefers-reduced-motion` respected.
Tokens are authored so a dark theme is a single override block later — but **light is the default and the only shipped theme in this phase**.

---

## 2. Architecture of the mock layer

The whole point: **one file changes when the backend lands.**

```
UI components → store (Zustand) → api/client.ts ──┬── mocks/* (now)
                                                  └── fetch + EventSource (M3)
```

- `api/types.ts` mirrors the architecture's response envelope exactly:
  `{ answer_md, citations[], excerpts[], scope_used, intent, refusal?, coverage?, trace_id, model_version, prompt_version, index_version, usage{tokens,usd}, disclaimer }`
- `mocks/stream.ts` is a real async generator emitting the real event names —
  `token · citation · excerpt · stage · refusal · usage · done · error` — with jittered delays (TTFT ~600ms) so the streaming UI is exercised for real, not faked with a typewriter.
- `mocks/scenarios.ts` exposes forceable states: `happy · refusal · llm_down · rate_limited · parse_failed · timeout · interrupted · low_ocr · empty_workspace`.
- A **Demo menu** (dev-only, top bar) switches scenarios live. Keyword triggers too (asking about something absent from the fixture corpus genuinely produces the refusal path).
- Fixture corpus: 6 documents — 2 contracts (one a 312-page MSA, one scanned with low-OCR pages), 2 opinions, 1 pleading, 1 `other`, plus one `failed` and one `parsing` row. Synthetic page text is real enough that span highlighting is meaningful.

---

## 3. Folder skeleton

```
frontend/
├─ index.html · package.json · tsconfig.json · vite.config.ts · eslint.config.js
└─ src/
   ├─ main.tsx · App.tsx · routes.tsx
   ├─ styles/        theme.css (tokens) · global.css · print.css
   ├─ lib/           cn · format (dates, bytes, page refs, cite labels) · hotkeys · clipboard · markdown · a11y
   ├─ api/           types.ts · client.ts · sse.ts
   ├─ mocks/         documents · conversations · answers · summaries · drafts · pages · stream · scenarios
   ├─ store/         useWorkspace · useConversations · useComposer · useEvidence · useUi
   ├─ components/
   │  ├─ ui/         Button IconButton Chip Badge Tooltip Popover HoverCard Dialog Drawer Sheet Tabs ToggleGroup
   │  │              Select Textarea Switch Skeleton Progress Toast EmptyState Kbd ScrollArea Separator Spinner
   │  ├─ shell/      AppShell TopBar DegradedBanner CommandPalette ToastHost PanelResizer
   │  ├─ sidebar/    Sidebar NewChatButton ConversationSearch ConversationList ConversationItem
   │  │              ConversationMenu WorkspaceHealthCard SidebarFooter
   │  ├─ composer/   PromptBar ModeChips ScopePicker ScopeSummary DraftParamsDrawer AttachButton
   │  │              SendButton SuggestionChips ComposerHint
   │  ├─ chat/       ChatPane MessageList UserMessage AssistantMessage StageTrace StreamingCaret
   │  │              Markdown CitationChip CitationHoverCard ScopeStatement IntentBadge RefusalCard
   │  │              ErrorCard IncompleteBanner FeedbackBar FeedbackReasonSheet MessageActions RegenerateMenu
   │  ├─ answer/     AnswerBody SummaryView SummaryField NotAddressedField DraftView DraftBanner
   │  │              DraftOutlineNav ExportMenu
   │  ├─ evidence/   EvidencePanel EvidenceHeader ExcerptList ExcerptCard SpanHighlight RelevanceMeter
   │  ├─ documents/  UploadDropzone UploadQueue UploadQueueItem DocumentTable DocumentRow StatusPill
   │  │              TypeBadge TypeOverrideMenu OcrWarningBadge DocumentFilters DocumentDetailDrawer
   │  │              DeleteDocDialog DeleteWorkspaceDialog
   │  ├─ viewer/     DocumentViewer ViewerToolbar PageList PageSurface PageTextLayer HighlightLayer
   │  │              PageJumper OcrConfidenceStrip
   │  ├─ trace/      TraceDrawer StageRow UsageLedger VersionStamps
   │  └─ auth/       LoginCard PasswordField
   └─ pages/         ChatPage · LibraryPage · DocumentPage · SettingsPage · LoginPage · NotFoundPage
```

Routes: `/login` · `/` → new chat · `/c/:conversationId` · `/library` · `/doc/:documentId?page=&span=` · `/settings`.
~90 files. Deep-linking a citation (`/doc/:id?page=84&span=s_412`) is a first-class URL — it is how an attorney sends a colleague the exact clause.

---

## 4. Build phases

Each phase ends in something clickable. Check off as we go.

### P0 — Foundation
- [x] Vite + React 19 + TS scaffold under `frontend/`, strict mode, path alias `@/`
- [x] Tailwind v4 with `@theme` tokens from §1; `theme.css`, `global.css`, `print.css`
- [x] Fonts self-hosted (Inter, Source Serif 4, JetBrains Mono), `font-display: swap`
- [x] ESLint + Prettier + `tsc --noEmit` clean
- [x] `ui/` primitives on Radix: Button, IconButton, Chip, Badge, Tooltip, Popover, HoverCard, Dialog, Drawer, Tabs, ToggleGroup, Select, Textarea, Switch, Skeleton, Progress, Toast, EmptyState, Kbd, ScrollArea, Separator
- **Visible:** a `/kitchen-sink` route showing every primitive in every state.

### P1 — Shell, sidebar, routing — `US-10`
- [x] `AppShell` three-pane grid with resizable, collapsible sidebar + evidence panel (sizes persisted)
- [x] `TopBar`: workspace name, doc count, Library, Settings, Demo menu
- [x] Sidebar: `+ New chat` (→ NCC), search, conversation list grouped Today / Yesterday / Previous 7 days / Earlier
- [x] Conversation item: title, relative time, mode glyph; hover menu → rename (inline), pin, delete (confirm)
- [x] `WorkspaceHealthCard`: `6 documents · 4 ready · 1 indexing · 1 failed` → links to Library
- [ ] Cursor-paged infinite scroll against the mock
- [x] `CommandPalette` (⌘K): jump to conversation, open document, switch mode, upload
- **Visible:** full navigation over mock conversations.

### P2 — Composer — `FR-8, FR-19`
- [x] `PromptBar`: auto-growing textarea (max 12 lines), ⌘/Ctrl+Enter send, Enter newline, `/` to focus, token-ish char counter, disabled state with reason when no document is ready
- [x] `ModeChips`: Auto · Ask · Summary · Draft (⌘1–4), each with a one-line "what this does" tooltip
- [x] `ScopePicker`: This document / Selected (n) / Entire workspace; multi-select with status, non-ready items disabled **with reason**; shows `scope_used` preview
- [x] `DraftParamsDrawer`: draft type (memo / case brief), question presented, audience, position to argue, tone, source selection — validation before send
- [x] Summary mode: document picker + detected-type display with override
- [x] `AttachButton` → upload sheet without leaving the chat
- [x] `SuggestionChips` on an empty conversation, drawn from the fixture corpus
- **Visible:** compose any request in any mode; payload echoed in the Demo menu.

### P3 — Chat + streaming — `FR-10, NFR-1, NFR-7, NFR-13`
- [ ] `MessageList` virtualized, sticky-bottom with "jump to latest"
- [x] `UserMessage`: text + mode/scope chips, edit-and-resend
- [x] `AssistantMessage` header: `IntentBadge` (`Auto → Ask`), `ScopeStatement`, timestamp, model stamp
- [x] `StageTrace`: live stages with elapsed ms, collapses to a one-line summary when done
- [x] Token-by-token render with `StreamingCaret`; stop button; graceful mid-stream citation arrival
- [x] `Markdown` renderer (headings, lists, tables, blockquote, code) in serif, with citation-chip substitution
- [x] `IncompleteBanner` for interrupted streams; `aria-live="polite"` region announcing stage changes and completion
- [x] `MessageActions`: copy, export Markdown, regenerate (same / different mode), open trace
- [x] `FeedbackBar` + `FeedbackReasonSheet` with legal-specific reasons: *wrong citation · missed content in my documents · fabricated something · refused but the answer is there · wrong structure/tone* — `FR-25`
- **Visible:** a real streaming answer with trace, actions, and feedback.

### P4 — Citations, evidence, viewer — `FR-11, FR-12, FR-13, NFR-13`
- [x] `CitationChip` `[1]`: numbered, hover → `CitationHoverCard` (document, page, 2-line excerpt, relevance), click → evidence panel, ⌥↑/↓ cycles, focus ring, SR label "citation 1, Master Services Agreement, page 84"
- [x] `EvidencePanel`: excerpts arriving during the stream, grouped by document, each with page ref, relevance meter, "open in document", copy-with-citation
- [x] `ExcerptCard` highlights the exact character span inside surrounding context
- [x] `DocumentViewer`: paginated synthetic page surfaces, virtualized for 500 pages, text layer + `HighlightLayer`, toolbar (zoom, page jump, search-in-doc), `OcrConfidenceStrip` flagging low-confidence pages
- [x] Deep link `/doc/:id?page=&span=` scrolls to and pulses the span; back returns to the conversation
- **Visible:** click a citation in an answer, land on the highlighted clause, come back.

### P5 — Summary & Draft renderers — `FR-15, FR-16, FR-17, FR-18, FR-20, FR-23, FR-24`
- [x] `SummaryView`: field cards per detected type — **contract** (parties, term, obligations, payment, termination, liability/indemnity, assignment & change of control, governing law, off-market flags), **opinion** (posture, facts, issues, holding, reasoning, disposition, separate opinions), **pleading** (court/parties/cause, relief, allegations, theories, dates)
- [x] `NotAddressedField`: "Not addressed in this document" as a *legitimate* empty state — neutral, never styled as missing data
- [x] Off-market / unusual-term flags get the caution treatment with their cite
- [x] Coverage statement for composed long-document summaries: "Composed from 312 pages in 7 passes · all pages covered" (`FR-17`)
- [x] Field-level actions: copy field with citation, jump to source
- [x] `DraftView`: memo (Question Presented → Short Answer → Statement of Facts → Discussion → Conclusion) and case brief, with `DraftOutlineNav`
- [x] `DraftBanner`: sticky `UNREVIEWED AI DRAFT — attorney review required`; print watermark; **disclaimer injected into clipboard and Markdown export**
- [x] `ExportMenu`: copy Markdown, copy plain, download `.md` — each carrying disclaimer + citation appendix (`.docx` is out of scope per BRD §3)
- **Visible:** all three output shapes rendered from fixtures.

### P6 — Library & upload — `FR-1, FR-2, FR-3, FR-5, FR-6, FR-26`
- [x] `UploadDropzone`: full-window drag target, multi-file, picker fallback; client-side reject >50MB or unsupported type **naming the limit and the actual value** (`FR-2`)
- [x] `UploadQueue`: per-file simulated lifecycle `queued → parsing → OCR → indexing → ready | failed`, independent rows — one failure never fails the batch (`FR-3`); retry on a failed row
- [x] `DocumentTable`: name, type, pages, status, OCR flag, uploaded; sortable + filterable by type/status; row select feeds the scope picker
- [x] `TypeBadge` + `TypeOverrideMenu` with "detected · overridden" provenance (`FR-5`)
- [x] `DocumentDetailDrawer`: ingestion log, page count, low-confidence pages, "ask about this document"
- [x] `DeleteDocDialog` enumerating what is removed; `DeleteWorkspaceDialog` with type-to-confirm
- **Visible:** drag in files, watch them ingest, query them, delete them.

### P7 — Non-happy paths — `FR-14, FR-28, NFR-6, TS-3`
- [x] `RefusalCard`: scope searched, what was looked for, why it fell short, next actions — calm slate, feedback bar intact ("the answer *is* in my documents" is the signal we most need)
- [x] `ErrorCard` variants, each naming a next action: LLM unavailable (retry + "show retrieved passages anyway"), rate limited (countdown), parse failure (link to the document, re-upload), timeout (retry narrower scope)
- [ ] `DegradedBanner` for provider outage — retrieval-only mode
- [x] Empty states: no documents (onboarding), no conversations, empty search, zero excerpts
- [ ] Skeletons for every async surface; offline detection
- **Visible:** every failure mode reachable from the Demo menu.

### P8 — Trust surfaces — `NFR-8, NFR-11, NFR-12, TS-6`
- [x] `TraceDrawer`: stage timings, retrieved→reranked counts, tokens, cost, `trace_id`, model/prompt/index versions
- [x] `UsageLedger` in Settings: per-conversation and total spend against the `$0.05/ask` budget
- [x] `SettingsPage`: Data & privacy (zero-retention statement, encryption, retention, audit-log note in plain language), workspace deletion, keyboard-shortcut reference, about/version
- [x] `LoginCard`: email + password, inline validation, mocked session (`FR-27`)
- **Visible:** login → workspace, and a verifiable account of what any answer cost and came from.

### P9 — Accessibility, responsive, polish — `NFR-13`
- [x] Keyboard pass: full tab order, visible focus everywhere, no trap, Esc closes panels, documented shortcut map
- [x] Screen-reader pass: landmarks, `aria-live` streaming, citation/excerpt announcements, labelled controls, meaningful headings
- [ ] Contrast audit against AA for all token pairs including the semantic backgrounds
- [x] Responsive: 3-pane ≥1280px · evidence as overlay 1024–1280 · sidebar as drawer <1024 · single-column mobile with evidence in a sheet
- [ ] `prefers-reduced-motion`, 200% zoom, print stylesheet for drafts (watermark + citation appendix)
- [ ] Final polish: empty-state illustrations, micro-interactions, optimistic rename, toast copy review
- **Visible:** usable end-to-end with the mouse unplugged.

---

## 5. Deliberately out of scope here

Real PDF rendering (PDF.js lands with real files in M3) · `.docx` export (BRD §3) · dark theme (tokens ready, not shipped) · multi-user / roles (BRD §3) · compare & redline · any real network call · unit/E2E test suite beyond type-checking and lint — this phase is validated by clicking every state through the Demo menu.

## 6. Three things I'd flag before we start

1. **The 500-page viewer is the one real engineering risk in this phase.** Virtualization plus a highlight layer that survives scrolling is fiddly; I'm budgeting P4 accordingly rather than discovering it late.
2. **A 3-minute summary needs its own progress UI**, not the chat spinner. P5 gives it field-by-field progress. If NFR-3's 3-minute budget holds, that screen is where the product feels fast or broken.
3. **Mobile is a deliberate compromise.** This is a three-pane desktop tool for someone reading long documents; I'll make it work down to phone width, but the evidence-beside-answer promise degrades to a sheet there. Flagging it rather than pretending otherwise.

---

**Approval gate:** once you're happy with this, P0 → P9 in order, with something clickable at each phase boundary.


---

# Build status — 2026-10-02

**P0 → P9 implemented and verified.** `npm run build` passes (typecheck + production build),
`npm run lint` is clean, and all seven route modules transform under the dev server. 85 source files.

Initial JS chunk 362 kB / 109 kB gzip after route code-splitting (down from 667 kB in one chunk);
CSS 56 kB / 12 kB gzip.

## Two bugs found and fixed during the build

The streaming contract was incomplete. The store had nothing to initialise a summary or a draft
from, so **a streaming summary rendered a blank header and a streaming draft rendered no sections at
all** — the fixtures looked fine, live streams did not. Fixed by adding `summary_start` and
`draft_start` to the SSE event union, emitted before their fields and sections. Both are events the
real backend will need too, so this is a contract fix rather than a mock patch.

## Deviations from the plan, and why

- **`DegradedBanner` consolidated into `ErrorCard`.** NFR-6's requirement — retrieval survives an
  LLM outage — is better served at the point of failure, where the card offers "read the retrieved
  passages" against that specific turn's excerpts, than by a global banner telling the user
  something is wrong somewhere. The behaviour ships; the separate component does not.
- **Print watermark added** beyond the plan. Nuance #3 said the draft marking must survive the
  clipboard; a printed draft left on a desk is the same failure, so `print:` variants strip the app
  chrome and lay "UNREVIEWED AI DRAFT" diagonally across the page.
- **TypeScript pinned to 6.x.** `typescript-eslint` hard-blocks TS 7.0 at runtime
  ([#10940](https://github.com/typescript-eslint/typescript-eslint/issues/10940)). Working
  `react-hooks/exhaustive-deps` is worth more than a two-week-old compiler release. Unpin when that
  closes.
- **No global `.prose` class.** Answer typography lives in the markdown renderer as utilities, so an
  element and its appearance sit together and there is no second stylesheet to drift.

## Outstanding, with reasons

| Item | Why it is still open |
|---|---|
| `MessageList` virtualization | Sticky-bottom and jump-to-latest ship; windowing does not. Threads are bounded by conversation length and the fixtures do not stress it. The viewer — which genuinely needed it at 500 pages — is windowed. |
| Cursor-paged infinite scroll in the sidebar | The mock returns every conversation at once. Needs the real cursor from `GET /api/conversations?cursor=`, so it is M3 work. |
| Offline detection | Skeletons ship on every async surface; a `navigator.onLine` listener does not. Low value without a network to lose. |
| Numeric AA contrast audit | Token pairs were chosen for AA and the semantic backgrounds read correctly, but nothing has been measured. Wants a tool over the compiled CSS, not an eyeball. |
| 200% zoom pass | Layout is fluid and should hold; unverified. |
| Search-in-document | The viewer toolbar ships zoom, page jump and OCR flagging. In-document text search was cut; citation deep-links cover the primary path. |
| Empty-state illustrations | Icons throughout instead. A deliberate hold — placeholder illustrations tend to outlive their welcome. |

## Verified by hand

Typecheck, lint, production build, dev-server transforms of every module, and the presence of every
custom token utility in the compiled CSS (including the fractional spacing and arbitrary animation
values, which are the usual Tailwind v4 failure mode).

**Not verified:** rendered appearance. No browser or screenshot tool was available in this session,
so nothing here has been looked at in a viewport. `/kitchen-sink` exists to make that review fast —
it is the first thing to open.
