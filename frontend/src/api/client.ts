import type {
  AssistantTurn,
  Conversation,
  ConversationPage,
  ConversationSummary,
  DocType,
  DocumentStatus,
  Feedback,
  LegalDocument,
  LoginResponse,
  Me,
  Page,
  RejectedFile,
  ServerConfig,
  Span,
  StreamEvent,
  Trace,
  TurnPage,
  UploadResult,
  UsageLedger,
  WorkspaceHealth,
} from './types'
import type { SendMessageRequest } from './types'
import { ApiErrorResponse, request } from './http'
import { streamEvents } from './sse'

/**
 * The only seam between the UI and the world.
 *
 * Every function here is one HTTP call against the FastAPI backend, and the
 * signatures are the contract: no component calls `fetch`, and no component
 * knows a URL. The two modules underneath — `http.ts` for JSON and `sse.ts` for
 * the stream — exist so that credentials, `ApiError` parsing and the 401 path
 * are decided once.
 *
 *   GET/POST/PATCH/DELETE  /api/conversations[/{id}[/messages]]
 *   POST                   /api/documents (multipart) · GET /api/documents
 *   POST                   /api/conversations/{id}/messages            (SSE)
 *   GET                    /api/spans/{id} · /api/documents/{id}/pages/{n}
 *   POST                   /api/feedback · GET /api/usage · /api/config
 *
 * `commitTurns` is gone, deliberately. The backend writes the user turn and the
 * assistant turn before the first byte streams and finalises the status in a
 * `finally` — `complete`, `refused`, `error` or `incomplete`. A client pushing
 * its own state back would be overwriting the record with a guess.
 */

/** 404 is a legitimate answer for "does this exist" — not an error to raise. */
async function orNull<T>(promise: Promise<T>): Promise<T | null> {
  try {
    return await promise
  } catch (thrown) {
    if (thrown instanceof ApiErrorResponse && thrown.status === 404) return null
    throw thrown
  }
}

/* ------------------------------------------------------------ validation */

export type FileLimits = Pick<ServerConfig, 'maxBytes' | 'acceptedExtensions'>

/**
 * FR-2: name the limit *and* the actual value, or the message is useless.
 *
 * Client-side validation is a courtesy — it puts the message on screen before a
 * 50 MB upload starts. The server re-validates everything, including a
 * magic-byte sniff the browser cannot do, so this is UX and that is the control.
 */
export function validateFile(file: File, limits: FileLimits): RejectedFile | null {
  const ext = `.${file.name.split('.').pop()?.toLowerCase() ?? ''}`
  if (!limits.acceptedExtensions.includes(ext)) {
    const accepted = limits.acceptedExtensions
      .map((e) => e.replace('.', '').toUpperCase())
      .join(', ')
    return {
      name: file.name,
      reason: `${ext === '.' ? 'This file type' : ext} is not supported. Legal Lens accepts ${accepted}.`,
    }
  }
  if (file.size > limits.maxBytes) {
    const mb = (file.size / 1024 / 1024).toFixed(1)
    const limit = Math.round(limits.maxBytes / 1024 / 1024)
    return { name: file.name, reason: `${mb} MB exceeds the ${limit} MB limit.` }
  }
  return null
}

/* ----------------------------------------------------------------- the api */

export const api = {
  /* ------------------------------------------------------ config & auth */

  /** Public endpoint: callable before sign-in, which is why limits live here. */
  getConfig(): Promise<ServerConfig> {
    return request<ServerConfig>('/api/config')
  },

  /** `null` means "not signed in" — the 401 the session bootstrap expects. */
  async me(): Promise<Me | null> {
    try {
      return await request<Me>('/api/auth/me', { allowUnauthorized: true })
    } catch (thrown) {
      if (thrown instanceof ApiErrorResponse && thrown.status === 401) return null
      throw thrown
    }
  },

  /**
   * FR-27: one user, email + password, server-side session.
   *
   * A 401 here means "wrong password", not "your session lapsed", so it must
   * not trip the global sign-out handler — hence `allowUnauthorized`.
   */
  async login(
    email: string,
    password: string,
  ): Promise<{ ok: true; me: Me } | { ok: false; error: string }> {
    try {
      const res = await request<LoginResponse>('/api/auth/login', {
        method: 'POST',
        body: { email, password },
        allowUnauthorized: true,
      })
      return { ok: true, me: { email: res.email, workspaceName: res.workspaceName } }
    } catch (thrown) {
      if (thrown instanceof ApiErrorResponse) return { ok: false, error: thrown.error.message }
      throw thrown
    }
  },

  /** Revokes the session row, not just the cookie. */
  logout(): Promise<void> {
    return request<void>('/api/auth/logout', { method: 'POST' })
  },

  /* --------------------------------------------------------- conversations */

  async listConversations(): Promise<ConversationSummary[]> {
    const page = await request<ConversationPage>('/api/conversations')
    return page.items
  },

  /** The paged form, for the sidebar's infinite scroll and palette search. */
  listConversationsPage(opts: { cursor?: string; limit?: number; q?: string } = {}) {
    return request<ConversationPage>('/api/conversations', {
      query: { cursor: opts.cursor, limit: opts.limit, q: opts.q },
    })
  },

  getConversation(id: string): Promise<Conversation | null> {
    return orNull(request<Conversation>(`/api/conversations/${id}`))
  },

  /** The NCC endpoint: POST /api/conversations. Returns 201 with empty turns. */
  createConversation(title = 'New chat'): Promise<Conversation> {
    return request<Conversation>('/api/conversations', { method: 'POST', body: { title } })
  },

  async renameConversation(id: string, title: string): Promise<void> {
    await request<ConversationSummary>(`/api/conversations/${id}`, {
      method: 'PATCH',
      body: { title },
    })
  },

  async setPinned(id: string, pinned: boolean): Promise<void> {
    await request<ConversationSummary>(`/api/conversations/${id}`, {
      method: 'PATCH',
      body: { pinned },
    })
  },

  async deleteConversation(id: string): Promise<void> {
    await request<void>(`/api/conversations/${id}`, { method: 'DELETE' })
  },

  /** Cursor-paged turns, for a thread too long to hydrate in one response. */
  listMessages(id: string, cursor?: string): Promise<TurnPage> {
    return request<TurnPage>(`/api/conversations/${id}/messages`, { query: { cursor } })
  },

  /* ------------------------------------------------------------ documents */

  listDocuments(): Promise<LegalDocument[]> {
    return request<LegalDocument[]>('/api/documents')
  },

  getDocument(id: string): Promise<LegalDocument | null> {
    return orNull(request<LegalDocument>(`/api/documents/${id}`))
  },

  /**
   * FR-1 / FR-3. One request for the whole drop: the endpoint is multi-file and
   * returns per-file outcomes, so one rejected file never fails the batch.
   *
   * A file whose SHA-256 matches something already ingested comes back in
   * `accepted` as the *existing* document, with no new job — so a caller that
   * polls must treat an already-terminal status as done rather than waiting for
   * a transition that will never come.
   */
  uploadDocuments(files: File[], signal?: AbortSignal): Promise<UploadResult> {
    const form = new FormData()
    for (const file of files) form.append('files', file, file.name)
    return request<UploadResult>('/api/documents', { method: 'POST', form, signal })
  },

  getDocumentStatus(id: string): Promise<DocumentStatus> {
    return request<DocumentStatus>(`/api/documents/${id}/status`)
  },

  /** FR-5: sets `typeSource: 'overridden'` server-side and returns the row. */
  setDocumentType(id: string, type: DocType): Promise<LegalDocument> {
    return request<LegalDocument>(`/api/documents/${id}`, { method: 'PATCH', body: { type } })
  },

  /** FR-26: file, extracted text, chunks and embeddings, effective immediately. */
  async deleteDocument(id: string): Promise<void> {
    await request<void>(`/api/documents/${id}`, { method: 'DELETE' })
  },

  /** FR-26 at workspace level. The server requires the typed confirmation. */
  async deleteWorkspace(): Promise<void> {
    await request<void>('/api/workspace', { method: 'DELETE', body: { confirm: 'DELETE' } })
  },

  /** Re-queues rather than mutating the failed job: the old log survives. */
  retryIngest(id: string): Promise<DocumentStatus> {
    return request<DocumentStatus>(`/api/documents/${id}/retry`, { method: 'POST' })
  },

  /* ----------------------------------------------------- pages and spans */

  getPage(documentId: string, page: number, signal?: AbortSignal): Promise<Page> {
    return request<Page>(`/api/documents/${documentId}/pages/${page}`, { signal })
  },

  getSpan(spanId: string): Promise<Span | null> {
    return orNull(request<Span>(`/api/spans/${spanId}`))
  },

  /* ---------------------------------------------------------- the stream */

  /**
   * POST /api/conversations/{id}/messages → text/event-stream.
   *
   * `onIds` hands back the server's message ids, which arrive as response
   * headers before the first token. The client renders optimistically with its
   * own ids, so without these `POST /api/feedback` would 404 on an id the
   * server has never seen.
   */
  sendMessage(
    req: SendMessageRequest,
    opts: {
      scenario?: string | null
      signal?: AbortSignal
      idempotencyKey?: string
      onIds?: (ids: { assistantTurnId?: string; userTurnId?: string }) => void
      onReplay?: (turn: AssistantTurn) => void
    } = {},
  ): AsyncGenerator<StreamEvent> {
    const { conversationId, ...body } = req
    return streamEvents(`/api/conversations/${conversationId}/messages`, {
      body: { ...body, conversationId },
      scenario: opts.scenario,
      signal: opts.signal,
      idempotencyKey: opts.idempotencyKey,
      onReplay: opts.onReplay,
      onOpen: (res) => {
        const assistantTurnId = res.headers.get('X-LegalLens-Turn-Id') ?? undefined
        const userTurnId = res.headers.get('X-LegalLens-User-Turn-Id') ?? undefined
        if (assistantTurnId || userTurnId) opts.onIds?.({ assistantTurnId, userTurnId })
      },
    })
  },

  /* ------------------------------------------------------------- the rest */

  /** FR-25. Linked to the response *and* its retrieval trace, server-side. */
  async submitFeedback(turnId: string, feedback: Feedback): Promise<void> {
    await request<void>('/api/feedback', {
      method: 'POST',
      body: { turnId, verdict: feedback.verdict, reasons: feedback.reasons, note: feedback.note },
    })
  },

  getUsage(): Promise<UsageLedger> {
    return request<UsageLedger>('/api/usage')
  },

  getWorkspaceHealth(): Promise<WorkspaceHealth> {
    return request<WorkspaceHealth>('/api/workspace/health')
  },

  /** NFR-11: the queries actually run and the candidate set with scores. */
  getTrace(traceId: string): Promise<Trace | null> {
    return orNull(request<Trace>(`/api/traces/${traceId}`))
  },

  /** Reconnect probe: did a stream we lost actually complete? */
  getTurn(turnId: string): Promise<AssistantTurn | null> {
    return orNull(request<AssistantTurn>(`/api/turns/${turnId}`))
  },
}

export type { RejectedFile }
