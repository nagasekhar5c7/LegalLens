/**
 * Wire types — mirrors the Pydantic models in the technical architecture.
 * These are the contract. When the FastAPI backend lands (M3), only
 * `api/client.ts` changes; nothing in this file should need to.
 */

/* ------------------------------------------------------------------ modes */

/** What the user asked for. `auto` defers to the intent router. */
export type Mode = 'auto' | 'ask' | 'summary' | 'draft'

/** What the router decided. `out_of_scope` short-circuits to a refusal. */
export type Intent = 'ask' | 'summary' | 'draft' | 'out_of_scope'

export const MODES: Mode[] = ['auto', 'ask', 'summary', 'draft']

/* -------------------------------------------------------------- documents */

export type DocType = 'contract' | 'opinion' | 'pleading' | 'other'

/** FR-3 lifecycle. Only `ready` documents enter retrieval (FR-7). */
export type DocStatus = 'queued' | 'parsing' | 'ocr' | 'indexing' | 'ready' | 'failed'

export interface OcrInfo {
  /** True when the source had no text layer and was OCR'd (DR-1). */
  scanned: boolean
  meanConfidence: number
  /** 1-indexed pages below the confidence floor — surfaced in the UI (DR-1). */
  lowConfidencePages: number[]
}

export interface IngestionStep {
  label: string
  status: 'done' | 'active' | 'pending' | 'failed'
  detail?: string
  ms?: number
}

export interface LegalDocument {
  id: string
  name: string
  type: DocType
  /** Provenance for the type badge: classifier output vs. user override (FR-5). */
  typeSource: 'detected' | 'overridden'
  classifierConfidence: number
  pages: number
  sizeBytes: number
  status: DocStatus
  /** 0–100 for the in-flight statuses. */
  progress?: number
  uploadedAt: string
  ocr?: OcrInfo
  failure?: DocumentFailure
  ingestion?: IngestionStep[]
  /** Set when an upload matched an already-ingested file by SHA-256. */
  duplicateOf?: string
}

export interface DocumentFailure {
  reason: string
  detail: string
}

/** The poll target during ingestion — deliberately smaller than the document. */
export interface DocumentStatus {
  id: string
  status: DocStatus
  pages: number
  progress?: number
  ocr?: OcrInfo
  failure?: DocumentFailure
  ingestion: IngestionStep[]
}

/* ------------------------------------------------------- spans & citations */

/** A resolvable location in a document: page + character range. FR-11. */
export interface Span {
  id: string
  documentId: string
  page: number
  charStart: number
  charEnd: number
  /** Clause path for contracts, e.g. "§ 12.3(b)" — FR-4. */
  section?: string
}

export interface Citation {
  /** Display number — the `[1]` in the answer body. */
  n: number
  spanId: string
  documentId: string
  documentName: string
  page: number
  section?: string
  /** Short human label, e.g. "MSA § 12.3, p. 84". */
  label: string
}

/** FR-12: shown beside the answer with no further user action. */
export interface Excerpt {
  spanId: string
  documentId: string
  documentName: string
  page: number
  section?: string
  /** Context before / after the cited span, for reading in situ. */
  before: string
  text: string
  after: string
  /** Reranker score, 0–1. Below the floor it would have been dropped. */
  relevance: number
}

/* ------------------------------------------------------------------ scope */

export type ScopeKind = 'document' | 'subset' | 'workspace'

export interface Scope {
  kind: ScopeKind
  documentIds: string[]
}

/** FR-9: echoed back on every response and stated in the UI. */
export interface ScopeUsed extends Scope {
  documentCount: number
  pageCount: number
  /** Documents excluded because they were not `ready` (FR-7). */
  excluded: { documentId: string; name: string; reason: string }[]
}

/* ------------------------------------------------------- stages and trace */

export type StageKey =
  | 'routing'
  | 'retrieving'
  | 'reranking'
  | 'expanding'
  | 'assembling'
  | 'writing'
  | 'verifying'
  | 'classifying'
  | 'field_retrieval'
  | 'mapping'
  | 'composing'

export interface Stage {
  key: StageKey
  label: string
  detail?: string
  status: 'active' | 'done'
  elapsedMs: number
}

/** NFR-11 / NFR-12: per-stage cost and the versions that produced the answer. */
export interface Usage {
  inputTokens: number
  outputTokens: number
  usd: number
  latencyMs: number
  timeToFirstTokenMs: number
}

export interface Versions {
  model: string
  prompt: string
  index: string
}

/* --------------------------------------------------------------- refusal */

/** FR-14 / TS-3. A first-class outcome, distinct in type from an error. */
export interface Refusal {
  kind: 'no_support' | 'out_of_scope' | 'below_threshold'
  /** Plain-language account of what was searched and why it fell short. */
  message: string
  /** The retrieval queries that were actually run. */
  lookedFor: string[]
  bestRelevance?: number
  threshold?: number
  suggestions: RefusalSuggestion[]
}

export interface RefusalSuggestion {
  kind: 'widen_scope' | 'upload' | 'rephrase' | 'switch_mode'
  label: string
  detail?: string
}

/* ----------------------------------------------------------------- errors */

/** FR-28: every variant names a next action. */
export type ApiErrorKind =
  | 'llm_unavailable'
  | 'rate_limited'
  | 'parse_failed'
  | 'timeout'
  | 'network'

export interface ApiError {
  kind: ApiErrorKind
  message: string
  /** What the user can do about it. */
  action: string
  retryAfterSec?: number
  documentId?: string
  /** NFR-6: retrieval survives an LLM outage, so excerpts may still exist. */
  excerptsAvailable?: boolean
}

/* ---------------------------------------------------- summary (FR-15/16) */

export interface SummaryField {
  key: string
  label: string
  /** Null means genuinely absent — rendered as "not addressed", never inferred. */
  valueMd: string | null
  citations: number[]
  /** Flags an off-market or unusual term for attention (BRD §9). */
  flag?: 'off_market' | 'unusual'
  flagNote?: string
}

/** FR-17: what a composed long-document summary actually covered. */
export interface Coverage {
  pagesCovered: number
  totalPages: number
  passes: number
  note: string
}

export interface SummaryPayload {
  documentId: string
  documentName: string
  documentType: DocType
  templateName: string
  fields: SummaryField[]
  coverage?: Coverage
}

/* ------------------------------------------------------ draft (FR-18/19) */

export type DraftType = 'memo' | 'case_brief'

export interface DraftParams {
  draftType: DraftType
  questionPresented: string
  audience: string
  position: string
  tone: string
  documentIds: string[]
}

export interface DraftSection {
  key: string
  heading: string
  bodyMd: string
  citations: number[]
}

export interface DraftPayload {
  draftType: DraftType
  title: string
  params: DraftParams
  sections: DraftSection[]
}

/* --------------------------------------------------------------- envelope */

/** The one response shape all three workflows return. */
export interface ResponseEnvelope {
  answerMd: string
  kind: 'answer' | 'summary' | 'draft'
  intent: Intent
  citations: Citation[]
  excerpts: Excerpt[]
  scopeUsed: ScopeUsed
  summary?: SummaryPayload
  draft?: DraftPayload
  refusal?: Refusal
  coverage?: Coverage
  traceId: string
  versions: Versions
  usage: Usage
  /** TS-5: present on every output, carried into every export. */
  disclaimer: string
}

/* ------------------------------------------------------------ SSE events */

/** Event names match the architecture's `text/event-stream` contract. */
export type StreamEvent =
  | { type: 'stage'; stage: Stage }
  | { type: 'intent'; intent: Intent; mode: Mode }
  | { type: 'scope'; scopeUsed: ScopeUsed }
  | { type: 'token'; text: string }
  | { type: 'citation'; citation: Citation }
  | { type: 'excerpt'; excerpt: Excerpt }
  /** Header for a summary, sent before its fields so the card can render. */
  | { type: 'summary_start'; summary: Omit<SummaryPayload, 'fields'>; fieldCount: number }
  | { type: 'summary_field'; field: SummaryField; index: number; total: number }
  /** Shell for a draft, sent before its sections arrive. */
  | { type: 'draft_start'; draft: Omit<DraftPayload, 'sections'> }
  | { type: 'draft_section'; section: DraftSection }
  | { type: 'refusal'; refusal: Refusal }
  | { type: 'usage'; usage: Usage; versions: Versions; traceId: string }
  | { type: 'coverage'; coverage: Coverage }
  | { type: 'done'; disclaimer: string }
  | { type: 'error'; error: ApiError }

/* ------------------------------------------------- conversations & turns */

export type TurnStatus = 'streaming' | 'complete' | 'refused' | 'error' | 'incomplete'

export type FeedbackVerdict = 'up' | 'down'

export type FeedbackReason =
  | 'wrong_citation'
  | 'missed_content'
  | 'fabricated'
  | 'wrong_refusal'
  | 'wrong_structure'
  | 'other'

export interface Feedback {
  verdict: FeedbackVerdict
  reasons: FeedbackReason[]
  note?: string
}

export interface UserTurn {
  id: string
  role: 'user'
  text: string
  mode: Mode
  scope: Scope
  draftParams?: DraftParams
  createdAt: string
}

export interface AssistantTurn {
  id: string
  role: 'assistant'
  status: TurnStatus
  kind: 'answer' | 'summary' | 'draft'
  /** The mode the user picked, kept so `auto → ask` can be shown (nuance #4). */
  requestedMode: Mode
  intent?: Intent
  text: string
  citations: Citation[]
  excerpts: Excerpt[]
  scopeUsed?: ScopeUsed
  stages: Stage[]
  summary?: SummaryPayload
  /** How many fields a streaming summary will end up with. */
  expectedFieldCount?: number
  draft?: DraftPayload
  refusal?: Refusal
  error?: ApiError
  coverage?: Coverage
  usage?: Usage
  versions?: Versions
  traceId?: string
  disclaimer?: string
  feedback?: Feedback
  createdAt: string
}

export type Turn = UserTurn | AssistantTurn

export interface ConversationSummary {
  id: string
  title: string
  lastMessageAt: string
  pinned: boolean
  /** Glyph in the sidebar: what this thread mostly is. */
  dominantKind: 'answer' | 'summary' | 'draft'
  turnCount: number
}

export interface Conversation extends ConversationSummary {
  turns: Turn[]
}

/* ----------------------------------------------------------- misc models */

export interface SendMessageRequest {
  conversationId: string
  text: string
  mode: Mode
  scope: Scope
  draftParams?: DraftParams
}

export interface Page {
  documentId: string
  page: number
  text: string
  ocrConfidence?: number
}

export interface UsageLedgerRow {
  conversationId: string
  title: string
  asks: number
  usd: number
  tokens: number
}

/**
 * NFR-8. The totals are the server's, computed over every row rather than over
 * the page the client happens to hold, and `budgetPerAskUsd` is the $0.05
 * figure the settings screen measures spend against.
 */
export interface UsageLedger {
  rows: UsageLedgerRow[]
  totalUsd: number
  totalTokens: number
  totalAsks: number
  meanUsdPerAsk: number
  budgetPerAskUsd: number
}

export interface WorkspaceHealth {
  documents: number
  ready: number
  indexing: number
  failed: number
  pages: number
}

/* ------------------------------------------------- server config & auth */

/**
 * FR-2: the rejection message has to name the *real* limit, so the client does
 * not keep a second copy of what the server enforces.
 */
export interface ServerConfig {
  maxBytes: number
  maxPages: number
  maxDocuments: number
  acceptedExtensions: string[]
  /** False in production — the Demo menu hides itself rather than lying. */
  scenariosEnabled: boolean
  generationBudgetS: number
}

export interface Me {
  email: string
  workspaceName: string
}

export interface LoginResponse extends Me {
  ok: true
}

/* --------------------------------------------------------------- uploads */

export interface RejectedFile {
  name: string
  reason: string
}

/** FR-3: one rejected file never fails the batch, so both lists come back. */
export interface UploadResult {
  accepted: LegalDocument[]
  rejected: RejectedFile[]
}

/* -------------------------------------------------------------- paging */

export interface Paged<T> {
  items: T[]
  nextCursor?: string | null
}

export type ConversationPage = Paged<ConversationSummary>
export type TurnPage = Paged<Turn>

/* --------------------------------------------------------------- traces */

export interface RetrievalCandidate {
  spanId: string
  documentId: string
  page: number
  score: number
  /** False for a candidate the reranker dropped below the floor. */
  kept: boolean
}

/** NFR-11: what ran, what was asked, what came back and what was kept. */
export interface Trace {
  traceId: string
  conversationId?: string | null
  messageId?: string | null
  stages: Stage[]
  queries: string[]
  candidates: RetrievalCandidate[]
  timings: Record<string, number>
  versions?: Versions
  createdAt?: string | null
}
