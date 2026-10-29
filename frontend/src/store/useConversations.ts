import { create } from 'zustand'
import type {
  ApiError,
  AssistantTurn,
  Conversation,
  ConversationSummary,
  Feedback,
  Mode,
  SendMessageRequest,
  Turn,
  UserTurn,
} from '@/api/types'
import { api } from '@/api/client'
import { isAbort, toApiError } from '@/api/http'
import { titleFromQuestion } from '@/lib/format'
import { useComposer } from './useComposer'
import { useEvidence } from './useEvidence'
import { useUi } from './useUi'
import { toast } from './useToasts'

/**
 * In-flight stream control, keyed by the *optimistic* assistant turn id.
 *
 * An abort rather than a flag: breaking out of the loop would leave the socket
 * open and the server believing the client is still reading, and the turn would
 * be recorded as `complete` when the user cut it off. NFR-7 is about what the
 * record says, not only about what the screen paints.
 */
const inFlight = new Map<string, AbortController>()

interface ConversationState {
  list: ConversationSummary[]
  listLoading: boolean
  nextCursor: string | null
  loadingMore: boolean
  /** The query the loaded page was fetched with, so paging keeps it. */
  query: string
  current: Conversation | null
  turns: Turn[]
  streamingTurnId: string | null
  loadList: () => Promise<void>
  loadMore: () => Promise<void>
  search: (q: string) => Promise<void>
  open: (id: string) => Promise<void>
  startNew: () => void
  send: (
    text: string,
    conversationId?: string,
    onCreated?: (id: string) => void,
  ) => Promise<string>
  stop: () => void
  regenerate: (turnId: string, mode?: Mode) => Promise<void>
  setFeedback: (turnId: string, feedback: Feedback) => Promise<void>
  rename: (id: string, title: string) => Promise<void>
  setPinned: (id: string, pinned: boolean) => Promise<void>
  remove: (id: string) => Promise<void>
}

function emptyAssistant(id: string, requestedMode: Mode): AssistantTurn {
  return {
    id,
    role: 'assistant',
    status: 'streaming',
    kind: requestedMode === 'summary' ? 'summary' : requestedMode === 'draft' ? 'draft' : 'answer',
    requestedMode,
    text: '',
    citations: [],
    excerpts: [],
    stages: [],
    createdAt: new Date().toISOString(),
  }
}

const NETWORK: ApiError = {
  kind: 'network',
  message: 'The request could not be completed.',
  action: 'Check your connection and retry.',
}

export const useConversations = create<ConversationState>((set, get) => ({
  list: [],
  listLoading: true,
  nextCursor: null,
  loadingMore: false,
  query: '',
  current: null,
  turns: [],
  streamingTurnId: null,

  loadList: async () => {
    set({ listLoading: true })
    try {
      const page = await api.listConversationsPage({ q: get().query || undefined })
      set({ list: page.items, nextCursor: page.nextCursor ?? null, listLoading: false })
    } catch (thrown) {
      set({ listLoading: false })
      if (!isAbort(thrown)) toast.error('Could not load your conversations', toApiError(thrown).action)
    }
  },

  /**
   * US-10: search runs on the server.
   *
   * The sidebar also filters what it holds, which is what makes typing feel
   * instant — but a filter can only ever find what has already been paged in,
   * and the point of a search box is to find the thread from March.
   */
  search: async (q) => {
    set({ query: q })
    try {
      const page = await api.listConversationsPage({ q: q || undefined })
      // Ignore a response that lost the race to a later keystroke.
      if (get().query !== q) return
      set({ list: page.items, nextCursor: page.nextCursor ?? null })
    } catch {
      // Keep whatever is on screen; the client-side filter still applies.
    }
  },

  /** US-10: the sidebar pages rather than loading a year of threads at once. */
  loadMore: async () => {
    const { nextCursor, loadingMore } = get()
    if (!nextCursor || loadingMore) return
    set({ loadingMore: true })
    try {
      const page = await api.listConversationsPage({
        cursor: nextCursor,
        q: get().query || undefined,
      })
      set((s) => ({
        // Dedupe: a conversation touched while paging can appear on two pages.
        list: [...s.list, ...page.items.filter((c) => !s.list.some((x) => x.id === c.id))],
        nextCursor: page.nextCursor ?? null,
        loadingMore: false,
      }))
    } catch {
      set({ loadingMore: false })
    }
  },

  open: async (id) => {
    try {
      const c = await api.getConversation(id)
      if (!c) {
        // Deleted in another tab. A new chat is a better landing place than an
        // empty pane with a title that no longer exists.
        set({ current: null, turns: [] })
        useEvidence.getState().clear()
        toast.info('That conversation no longer exists', 'It may have been deleted elsewhere.')
        return
      }
      set({ current: c, turns: c.turns ?? [] })
      // Seed the evidence panel from the newest assistant turn with citations.
      const last = [...(c.turns ?? [])]
        .reverse()
        .find((t): t is AssistantTurn => t.role === 'assistant' && t.citations.length > 0)
      if (last) useEvidence.getState().setTurn(last.id, last.citations, last.excerpts)
      else useEvidence.getState().clear()
    } catch (thrown) {
      if (!isAbort(thrown)) toast.error('Could not open that conversation', toApiError(thrown).action)
    }
  },

  startNew: () => {
    set({ current: null, turns: [], streamingTurnId: null })
    useEvidence.getState().clear()
    useComposer.getState().reset()
  },

  send: async (text, conversationId, onCreated) => {
    const composer = useComposer.getState()
    const { scenario } = useUi.getState()

    let convId = conversationId ?? get().current?.id
    if (!convId) {
      // The NCC endpoint. The title is sent on create, which is also why the
      // server's own `autotitle` correctly no-ops: naming the thread here saves
      // a PATCH on the path to the first token.
      const created = await api.createConversation(
        titleFromQuestion(text || composer.draftParams.questionPresented),
      )
      convId = created.id
      set({ current: created, turns: [] })
      // Report it now: the caller can route to /c/{id} while the stream runs.
      onCreated?.(convId)
      void get().loadList()
    }

    // Summary operates on one document; Draft carries its own source selection.
    const scope =
      composer.mode === 'summary' && composer.summaryDocumentId
        ? { kind: 'document' as const, documentIds: [composer.summaryDocumentId] }
        : composer.mode === 'draft'
          ? { kind: 'subset' as const, documentIds: composer.draftParams.documentIds }
          : composer.scope

    const user: UserTurn = {
      id: `u_${Date.now().toString(36)}`,
      role: 'user',
      text: text || composer.draftParams.questionPresented,
      mode: composer.mode,
      scope,
      draftParams: composer.mode === 'draft' ? composer.draftParams : undefined,
      createdAt: new Date().toISOString(),
    }
    const assistantId = `a_${Date.now().toString(36)}`
    const assistant = emptyAssistant(assistantId, composer.mode)

    set((s) => ({ turns: [...s.turns, user, assistant], streamingTurnId: assistantId }))
    useComposer.getState().setText('')
    useEvidence.getState().setTurn(assistantId, [], [])

    /**
     * Patch the streaming turn.
     *
     * Matches on the *current* id rather than closing over `assistantId`, so it
     * keeps working after the server's id is adopted mid-stream.
     */
    let currentId = assistantId
    const patch = (fn: (t: AssistantTurn) => AssistantTurn) =>
      set((s) => ({
        turns: s.turns.map((t) => (t.id === currentId && t.role === 'assistant' ? fn(t) : t)),
      }))

    const controller = new AbortController()
    inFlight.set(assistantId, controller)

    const req: SendMessageRequest = {
      conversationId: convId,
      text: user.text,
      mode: composer.mode,
      scope,
      draftParams: composer.mode === 'draft' ? composer.draftParams : undefined,
    }

    let aborted = false
    try {
      const stream = api.sendMessage(req, {
        scenario,
        signal: controller.signal,
        // One fresh key per send. `regenerate` therefore gets its own, which is
        // the point: reusing a key would replay the old answer instead of
        // producing a new one.
        idempotencyKey: crypto.randomUUID(),
        /* The server's persisted ids, which arrive with the response headers
           before the first token. Rewriting by id (never by index) keeps this
           correct if the user has queued other work in between. */
        onIds: ({ assistantTurnId, userTurnId }) => {
          if (!assistantTurnId && !userTurnId) return
          set((s) => ({
            turns: s.turns.map((t) => {
              if (t.id === user.id && userTurnId) return { ...t, id: userTurnId }
              if (t.id === currentId && assistantTurnId) return { ...t, id: assistantTurnId }
              return t
            }),
            streamingTurnId: assistantTurnId ?? s.streamingTurnId,
          }))
          if (assistantTurnId) {
            const evidence = useEvidence.getState()
            if (evidence.turnId === currentId) {
              evidence.setTurn(assistantTurnId, evidence.citations, evidence.excerpts)
            }
            currentId = assistantTurnId
            inFlight.delete(assistantId)
            inFlight.set(assistantTurnId, controller)
          }
        },
        onReplay: (turn) => {
          // Unreachable with a fresh key, but a stored turn is still the truth.
          patch(() => turn)
        },
      })

      for await (const ev of stream) {
        switch (ev.type) {
          case 'stage':
            patch((t) => ({
              ...t,
              stages:
                ev.stage.status === 'done'
                  ? t.stages.map((s) => (s.key === ev.stage.key ? ev.stage : s))
                  : [...t.stages, ev.stage],
            }))
            break
          case 'intent':
            patch((t) => ({
              ...t,
              intent: ev.intent,
              kind: ev.intent === 'summary' ? 'summary' : ev.intent === 'draft' ? 'draft' : 'answer',
            }))
            break
          case 'scope':
            patch((t) => ({ ...t, scopeUsed: ev.scopeUsed }))
            break
          case 'token':
            patch((t) => ({ ...t, text: t.text + ev.text }))
            break
          case 'citation':
            patch((t) => ({
              ...t,
              citations: t.citations.some((c) => c.n === ev.citation.n)
                ? t.citations
                : [...t.citations, ev.citation],
            }))
            break
          case 'excerpt':
            patch((t) => ({
              ...t,
              excerpts: t.excerpts.some((x) => x.spanId === ev.excerpt.spanId)
                ? t.excerpts
                : [...t.excerpts, ev.excerpt],
            }))
            break
          case 'summary_start':
            patch((t) => ({
              ...t,
              kind: 'summary',
              summary: { ...ev.summary, fields: [] },
              expectedFieldCount: ev.fieldCount,
            }))
            break
          case 'summary_field':
            patch((t) => {
              if (!t.summary) return t
              const fields = [...t.summary.fields]
              fields[ev.index] = ev.field
              return { ...t, summary: { ...t.summary, fields } }
            })
            break
          case 'draft_start':
            patch((t) => ({
              ...t,
              kind: 'draft',
              draft: { ...ev.draft, sections: [] },
            }))
            break
          case 'draft_section':
            patch((t) =>
              t.draft
                ? {
                    ...t,
                    draft: {
                      ...t.draft,
                      sections: t.draft.sections.some((x) => x.key === ev.section.key)
                        ? t.draft.sections
                        : [...t.draft.sections, ev.section],
                    },
                  }
                : t,
            )
            break
          case 'coverage':
            patch((t) => ({ ...t, coverage: ev.coverage }))
            break
          case 'refusal':
            patch((t) => ({ ...t, refusal: ev.refusal, status: 'refused' }))
            break
          case 'usage':
            patch((t) => ({
              ...t,
              usage: ev.usage,
              versions: ev.versions,
              traceId: ev.traceId,
            }))
            break
          case 'error':
            patch((t) => ({
              ...t,
              error: ev.error,
              // NFR-7: a partial answer is never presented as complete.
              status: t.text.length > 0 ? 'incomplete' : 'error',
            }))
            break
          case 'done':
            patch((t) => ({
              ...t,
              disclaimer: ev.disclaimer,
              status: t.status === 'refused' ? 'refused' : 'complete',
            }))
            break
        }

        // Keep the evidence panel live as citations and excerpts arrive (FR-12).
        if (ev.type === 'citation' || ev.type === 'excerpt') {
          const t = get().turns.find((x) => x.id === currentId)
          if (t && t.role === 'assistant') useEvidence.getState().setTurn(t.id, t.citations, t.excerpts)
        }
      }
    } catch (thrown) {
      if (isAbort(thrown)) {
        aborted = true
      } else {
        patch((t) => ({ ...t, status: 'error', error: toApiError(thrown) ?? NETWORK }))
      }
    } finally {
      inFlight.delete(assistantId)
      inFlight.delete(currentId)
      set({ streamingTurnId: null })

      if (aborted) {
        /**
         * Adopt the server's status rather than guessing at it.
         *
         * The server has just written this turn — `incomplete` when there was
         * partial output, and it puts FR-24's "INCOMPLETE OUTPUT" marking into
         * the stored text itself so the marking survives a copy and an export.
         * Guessing here produced a turn that disagreed with its own record in
         * the one case that matters (stopped before anything arrived), so this
         * reads it back instead. One request, on a deliberate user action.
         */
        const serverId = currentId === assistantId ? null : currentId
        patch((t) => ({
          ...t,
          status: t.text.length > 0 || t.summary || t.draft ? 'incomplete' : 'complete',
        }))
        if (serverId) {
          void api
            .getTurn(serverId)
            .then((stored) => {
              if (stored) patch((t) => ({ ...t, status: stored.status, text: stored.text }))
            })
            .catch(() => {
              // Keep the optimistic status; it is the better of two guesses.
            })
        }
      }

      // The turn is already persisted — this is only the sidebar catching up on
      // the title, the turn count and the ordering.
      void get().loadList()

      const t = get().turns.find((x) => x.id === currentId)
      if (t && t.role === 'assistant' && (t.citations.length > 0 || t.excerpts.length > 0)) {
        useEvidence.getState().setTurn(t.id, t.citations, t.excerpts)
      }
    }

    return convId
  },

  stop: () => {
    const id = get().streamingTurnId
    if (!id) return
    inFlight.get(id)?.abort()
  },

  regenerate: async (turnId, mode) => {
    const turns = get().turns
    const idx = turns.findIndex((t) => t.id === turnId)
    const user = [...turns.slice(0, idx)].reverse().find((t): t is UserTurn => t.role === 'user')
    if (!user) return
    if (mode) useComposer.getState().setMode(mode)
    else useComposer.getState().setMode(user.mode)
    useComposer.getState().setScope(user.scope)
    if (user.draftParams) useComposer.setState({ draftParams: user.draftParams })
    set({ turns: turns.slice(0, idx - 1 < 0 ? 0 : idx - 1) })
    await get().send(user.text, get().current?.id)
  },

  setFeedback: async (turnId, feedback) => {
    const previous = get().turns.find((t) => t.id === turnId)
    set((s) => ({
      turns: s.turns.map((t) => (t.id === turnId && t.role === 'assistant' ? { ...t, feedback } : t)),
    }))
    try {
      await api.submitFeedback(turnId, feedback)
      toast.done(
        feedback.verdict === 'up' ? 'Marked as useful' : 'Thanks — logged against this retrieval trace',
        feedback.verdict === 'down'
          ? 'Feedback is linked to the trace id so the retrieval can be replayed.'
          : undefined,
      )
    } catch (thrown) {
      // Roll back: a thumb that silently failed to record is worse than one
      // that says it did not.
      if (previous && previous.role === 'assistant') {
        const restore = previous.feedback
        set((s) => ({
          turns: s.turns.map((t) =>
            t.id === turnId && t.role === 'assistant' ? { ...t, feedback: restore } : t,
          ),
        }))
      }
      toast.error('Feedback was not recorded', toApiError(thrown).action)
    }
  },

  rename: async (id, title) => {
    try {
      await api.renameConversation(id, title)
      set((s) => ({
        list: s.list.map((c) => (c.id === id ? { ...c, title } : c)),
        current: s.current?.id === id ? { ...s.current, title } : s.current,
      }))
    } catch (thrown) {
      toast.error('Could not rename that conversation', toApiError(thrown).action)
    }
  },

  setPinned: async (id, pinned) => {
    try {
      await api.setPinned(id, pinned)
      set((s) => ({ list: s.list.map((c) => (c.id === id ? { ...c, pinned } : c)) }))
    } catch (thrown) {
      toast.error('Could not pin that conversation', toApiError(thrown).action)
    }
  },

  remove: async (id) => {
    try {
      await api.deleteConversation(id)
      set((s) => ({
        list: s.list.filter((c) => c.id !== id),
        ...(s.current?.id === id ? { current: null, turns: [] } : {}),
      }))
    } catch (thrown) {
      toast.error('Could not delete that conversation', toApiError(thrown).action)
    }
  },
}))
