import { create } from 'zustand'
import type { DraftParams, DraftType, Mode, Scope } from '@/api/types'

interface ComposerState {
  text: string
  mode: Mode
  scope: Scope
  draftParams: DraftParams
  /** Document chosen for Summary mode, which operates on exactly one. */
  summaryDocumentId: string | null
  setText: (t: string) => void
  setMode: (m: Mode) => void
  setScope: (s: Scope) => void
  setSummaryDocumentId: (id: string | null) => void
  patchDraftParams: (p: Partial<DraftParams>) => void
  setDraftType: (t: DraftType) => void
  reset: () => void
}

const DEFAULT_DRAFT: DraftParams = {
  draftType: 'memo',
  questionPresented: '',
  audience: 'supervising partner',
  position: '',
  tone: 'neutral predictive',
  documentIds: [],
}

export const useComposer = create<ComposerState>((set) => ({
  text: '',
  mode: 'auto',
  /** FR-9: workspace is the default scope, narrowing when a doc is selected. */
  scope: { kind: 'workspace', documentIds: [] },
  draftParams: DEFAULT_DRAFT,
  summaryDocumentId: null,
  setText: (text) => set({ text }),
  setMode: (mode) => set({ mode }),
  setScope: (scope) => set({ scope }),
  setSummaryDocumentId: (summaryDocumentId) => set({ summaryDocumentId }),
  patchDraftParams: (p) => set((s) => ({ draftParams: { ...s.draftParams, ...p } })),
  setDraftType: (draftType) => set((s) => ({ draftParams: { ...s.draftParams, draftType } })),
  reset: () => set({ text: '', draftParams: DEFAULT_DRAFT }),
}))

/** What must be true before Send does anything useful, per mode. */
export function composerBlocker(
  mode: Mode,
  text: string,
  scope: Scope,
  draftParams: DraftParams,
  summaryDocumentId: string | null,
  readyCount: number,
): string | null {
  if (readyCount === 0)
    return 'No document is ready to search. Upload one, or wait for indexing to finish.'
  if (mode === 'summary' && !summaryDocumentId) return 'Choose the document to summarise.'
  if (mode === 'draft') {
    if (draftParams.documentIds.length === 0) return 'Choose at least one source document for the draft.'
    if (!draftParams.questionPresented.trim() && !text.trim())
      return 'State the question the draft should answer.'
    return null
  }
  if (!text.trim()) return null
  if (scope.kind !== 'workspace' && scope.documentIds.length === 0)
    return 'Select at least one document, or switch the scope to the whole workspace.'
  return null
}
