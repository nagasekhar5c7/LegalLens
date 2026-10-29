import { create } from 'zustand'
import type { Citation, Excerpt } from '@/api/types'

interface EvidenceState {
  /** Citations and excerpts of the turn currently being verified. */
  turnId: string | null
  citations: Citation[]
  excerpts: Excerpt[]
  /** The citation under inspection — drives highlight and panel scroll. */
  activeSpanId: string | null
  /** Index for ⌥↑/↓ cycling (nuance: citations are keyboard-navigable). */
  focusIndex: number
  setTurn: (turnId: string, citations: Citation[], excerpts: Excerpt[]) => void
  setActive: (spanId: string | null) => void
  cycle: (delta: number) => Citation | null
  clear: () => void
}

export const useEvidence = create<EvidenceState>((set, get) => ({
  turnId: null,
  citations: [],
  excerpts: [],
  activeSpanId: null,
  focusIndex: -1,

  setTurn: (turnId, citations, excerpts) => {
    const same = get().turnId === turnId
    set({
      turnId,
      citations,
      excerpts,
      activeSpanId: same ? get().activeSpanId : null,
      focusIndex: same ? get().focusIndex : -1,
    })
  },

  setActive: (activeSpanId) =>
    set((s) => ({
      activeSpanId,
      focusIndex: activeSpanId ? s.citations.findIndex((c) => c.spanId === activeSpanId) : -1,
    })),

  cycle: (delta) => {
    const { citations, focusIndex } = get()
    if (citations.length === 0) return null
    const next = (focusIndex + delta + citations.length * 2) % citations.length
    const citation = citations[next]
    set({ focusIndex: next, activeSpanId: citation.spanId })
    return citation
  },

  clear: () => set({ turnId: null, citations: [], excerpts: [], activeSpanId: null, focusIndex: -1 }),
}))
