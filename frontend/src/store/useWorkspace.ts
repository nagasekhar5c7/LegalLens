import { create } from 'zustand'
import type { DocStatus, DocType, LegalDocument, WorkspaceHealth } from '@/api/types'
import { api, validateFile, type RejectedFile } from '@/api/client'
import { toApiError } from '@/api/http'
import { useConfig } from './useConfig'
import { toast } from './useToasts'

export interface UploadRow {
  key: string
  name: string
  sizeBytes: number
  /** Independent per row: one failure must not fail the batch (FR-3). */
  state: 'validating' | 'rejected' | 'ingesting' | 'done' | 'failed'
  reason?: string
  documentId?: string
  progress: number
}

/** FR-7: only `ready` documents enter retrieval, so these are the waiting room. */
const IN_FLIGHT: DocStatus[] = ['queued', 'parsing', 'ocr', 'indexing']

export const isInFlight = (d: LegalDocument) => IN_FLIGHT.includes(d.status)

/**
 * How often to re-read the library while anything is ingesting.
 *
 * One list call per tick rather than one `/status` call per document: an
 * eight-file drop polled per-document at one second is 480 reads a minute
 * against a 600-a-minute limit, and the list response carries the full
 * ingestion step log the detail drawer wants anyway.
 */
const POLL_MS = 1_500

interface WorkspaceState {
  documents: LegalDocument[]
  loading: boolean
  /** Server-side counts, authoritative where the derived ones are a guess. */
  health: WorkspaceHealth | null
  uploads: UploadRow[]
  load: () => Promise<void>
  upload: (files: File[]) => Promise<void>
  clearUploads: () => void
  setType: (id: string, type: DocType) => Promise<void>
  remove: (id: string) => Promise<void>
  removeAll: () => Promise<void>
  retry: (id: string) => Promise<void>
}

export const useWorkspace = create<WorkspaceState>((set, get) => ({
  documents: [],
  loading: true,
  health: null,
  uploads: [],

  load: async () => {
    set({ loading: true })
    try {
      const documents = await api.listDocuments()
      set({ documents, loading: false })
      ensurePolling()
    } catch (thrown) {
      set({ loading: false })
      toast.error('Could not load your documents', toApiError(thrown).action)
    }
    // Not fatal if it fails: the sidebar falls back to the derived counts.
    void api
      .getWorkspaceHealth()
      .then((health) => set({ health }))
      .catch(() => undefined)
  },

  /**
   * FR-1 / FR-2 / FR-3.
   *
   * One multipart request for the whole drop — the endpoint is multi-file and
   * returns per-file outcomes, so a `.pptx` in the middle of a batch comes back
   * in `rejected` while everything else ingests. Progress then arrives by
   * polling, because ingestion is a job on the server rather than a promise in
   * this tab: closing the browser does not stop it.
   */
  upload: async (files) => {
    const limits = useConfig.getState().config
    const rows: UploadRow[] = files.map((f, i) => ({
      key: `${Date.now()}_${i}_${f.name}`,
      name: f.name,
      sizeBytes: f.size,
      state: 'validating',
      progress: 0,
    }))
    set((s) => ({ uploads: [...s.uploads, ...rows] }))

    const patch = (key: string, next: Partial<UploadRow>) =>
      set((s) => ({ uploads: s.uploads.map((r) => (r.key === key ? { ...r, ...next } : r)) }))

    // Client-side validation is a courtesy that puts the message on screen
    // before a 50 MB upload starts; the server re-validates regardless.
    const sending: { row: UploadRow; file: File }[] = []
    files.forEach((file, i) => {
      const row = rows[i]
      const rejection: RejectedFile | null = validateFile(file, limits)
      if (rejection) patch(row.key, { state: 'rejected', reason: rejection.reason })
      else sending.push({ row, file })
    })

    if (sending.length > 0) {
      for (const { row } of sending) patch(row.key, { state: 'ingesting' })
      try {
        const result = await api.uploadDocuments(sending.map((s) => s.file))

        // Match outcomes back to rows by name. The server preserves order
        // within `accepted`, but a rejection in the middle shifts it, so the
        // name is the only honest key.
        const accepted = new Map(result.accepted.map((d) => [d.name, d]))
        const rejected = new Map(result.rejected.map((r) => [r.name, r.reason]))

        for (const { row, file } of sending) {
          const doc = accepted.get(file.name)
          const reason = rejected.get(file.name)
          if (doc) {
            patch(row.key, {
              documentId: doc.id,
              progress: doc.progress ?? 0,
              // A duplicate comes back already `ready`: there is nothing to
              // wait for, and polling for a transition would never finish.
              state: isInFlight(doc) ? 'ingesting' : doc.status === 'failed' ? 'failed' : 'done',
              ...(doc.status === 'failed' ? { reason: doc.failure?.reason } : {}),
            })
          } else if (reason) {
            patch(row.key, { state: 'rejected', reason })
          } else {
            patch(row.key, {
              state: 'failed',
              reason: 'The server did not report an outcome for this file.',
            })
          }
        }

        mergeDocuments(set, result.accepted)
      } catch (thrown) {
        const error = toApiError(thrown)
        for (const { row } of sending) patch(row.key, { state: 'failed', reason: error.message })
      }
    }

    ensurePolling()
    reportBatch(get)
  },

  clearUploads: () => set({ uploads: [] }),

  setType: async (id, type) => {
    try {
      // FR-5: the server sets `typeSource: 'overridden'` and returns the row,
      // so there is nothing to guess and no second read.
      const updated = await api.setDocumentType(id, type)
      mergeDocuments(set, [updated])
    } catch (thrown) {
      toast.error('Could not change the document type', toApiError(thrown).action)
    }
  },

  remove: async (id) => {
    const doc = get().documents.find((d) => d.id === id)
    try {
      await api.deleteDocument(id)
      set((s) => ({ documents: s.documents.filter((d) => d.id !== id) }))
      if (doc) {
        toast.done(
          `Deleted “${doc.name}”`,
          'File, extracted text, chunks and embeddings removed.',
        )
      }
      void get().load()
    } catch (thrown) {
      toast.error('Could not delete that document', toApiError(thrown).action)
    }
  },

  removeAll: async () => {
    try {
      await api.deleteWorkspace()
      set({ documents: [], uploads: [], health: null })
      toast.done('Workspace deleted', 'Every document, conversation and trace is gone.')
    } catch (thrown) {
      toast.error('Could not delete the workspace', toApiError(thrown).action)
    }
  },

  retry: async (id) => {
    try {
      const status = await api.retryIngest(id)
      set((s) => ({
        documents: s.documents.map((d) =>
          d.id === id
            ? { ...d, status: status.status, progress: status.progress, failure: undefined }
            : d,
        ),
      }))
      ensurePolling()
    } catch (thrown) {
      toast.error('Could not retry ingestion', toApiError(thrown).action)
    }
  },
}))

/* ------------------------------------------------------------- the poll */

type Setter = (partial: Partial<WorkspaceState>) => void

/** Upsert by id, preserving list order for documents we already hold. */
function mergeDocuments(set: Setter, incoming: LegalDocument[]): void {
  const { documents } = useWorkspace.getState()
  const byId = new Map(incoming.map((d) => [d.id, d]))
  const updated = documents.map((d) => byId.get(d.id) ?? d)
  const added = incoming.filter((d) => !documents.some((x) => x.id === d.id))
  set({ documents: [...added, ...updated] })
}

/**
 * A single-flight timer, module-scoped on purpose.
 *
 * The store outlives every component, and ingestion outlives the route that
 * started it: a user who drops eight files and walks to the settings page
 * should come back to a finished library. Driving this from a `useEffect`
 * somewhere would tie a server-side job to a mounted component, and an
 * interval that fires while the previous request is still out is the classic
 * way this becomes a request storm on a slow machine.
 */
let timer: number | null = null
let polling = false

function ensurePolling(): void {
  if (timer !== null) return
  if (!useWorkspace.getState().documents.some(isInFlight)) return

  timer = window.setInterval(() => {
    if (polling) return
    const state = useWorkspace.getState()
    if (!state.documents.some(isInFlight)) {
      stopPolling()
      void api
        .getWorkspaceHealth()
        .then((health) => useWorkspace.setState({ health }))
        .catch(() => undefined)
      return
    }

    polling = true
    void api
      .listDocuments()
      .then((documents) => {
        useWorkspace.setState({ documents })
        // Keep the upload rows in step: this is what moves the progress bar.
        const byId = new Map(documents.map((d) => [d.id, d]))
        useWorkspace.setState((s) => ({
          uploads: s.uploads.map((row) => {
            const doc = row.documentId ? byId.get(row.documentId) : undefined
            if (!doc || row.state === 'rejected') return row
            if (isInFlight(doc)) return { ...row, progress: doc.progress ?? row.progress }
            return doc.status === 'failed'
              ? { ...row, state: 'failed', reason: doc.failure?.reason, progress: 100 }
              : { ...row, state: 'done', progress: 100 }
          }),
        }))
      })
      .catch(() => {
        // A transient failure should not kill the poll; the next tick retries.
      })
      .finally(() => {
        polling = false
      })
  }, POLL_MS)
}

function stopPolling(): void {
  if (timer !== null) window.clearInterval(timer)
  timer = null
}

function reportBatch(get: () => WorkspaceState): void {
  const rows = get().uploads
  const queued = rows.filter((r) => r.state === 'ingesting' || r.state === 'done').length
  const bad = rows.filter((r) => r.state === 'rejected' || r.state === 'failed').length
  if (queued > 0) {
    toast.done(
      `${queued} document${queued === 1 ? '' : 's'} uploaded`,
      bad > 0
        ? `${bad} could not be added — see the upload list.`
        : 'Text extraction and indexing are running on the server.',
    )
  } else if (bad > 0) {
    toast.error(
      `${bad} file${bad === 1 ? '' : 's'} could not be added`,
      'See the upload list for the reason on each one.',
    )
  }
}

/**
 * Derived state is a pure function, deliberately NOT a store method.
 *
 * A selector like `useWorkspace((s) => s.health())` returns a fresh object on
 * every call, and zustand 5 reads through `useSyncExternalStore` — so React
 * sees a new snapshot each render and loops, logging "the result of
 * getSnapshot should be cached". Components subscribe to `documents` (a stable
 * reference) and memoise this instead.
 */
export function workspaceHealth(documents: LegalDocument[]): WorkspaceHealth {
  return {
    documents: documents.length,
    ready: documents.filter((d) => d.status === 'ready').length,
    indexing: documents.filter(isInFlight).length,
    failed: documents.filter((d) => d.status === 'failed').length,
    pages: documents.filter((d) => d.status === 'ready').reduce((n, d) => n + d.pages, 0),
  }
}
