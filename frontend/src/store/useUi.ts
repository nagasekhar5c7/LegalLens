import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Scenario } from '@/lib/scenarios'

interface UiState {
  sidebarOpen: boolean
  evidenceOpen: boolean
  sidebarWidth: number
  evidenceWidth: number
  paletteOpen: boolean
  uploadOpen: boolean
  traceTurnId: string | null
  /** Dev-only scenario forcing, surfaced through the Demo menu. */
  scenario: Scenario
  toggleSidebar: () => void
  toggleEvidence: () => void
  setEvidenceOpen: (open: boolean) => void
  setSidebarWidth: (w: number) => void
  setEvidenceWidth: (w: number) => void
  setPaletteOpen: (open: boolean) => void
  setUploadOpen: (open: boolean) => void
  setTraceTurnId: (id: string | null) => void
  setScenario: (s: Scenario) => void
}

/** Panel sizes persist: re-sizing a rail every session is a papercut. */
export const useUi = create<UiState>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      evidenceOpen: true,
      sidebarWidth: 272,
      evidenceWidth: 400,
      paletteOpen: false,
      uploadOpen: false,
      traceTurnId: null,
      scenario: 'auto',
      toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
      toggleEvidence: () => set((s) => ({ evidenceOpen: !s.evidenceOpen })),
      setEvidenceOpen: (evidenceOpen) => set({ evidenceOpen }),
      setSidebarWidth: (w) => set({ sidebarWidth: Math.min(420, Math.max(208, w)) }),
      setEvidenceWidth: (w) => set({ evidenceWidth: Math.min(640, Math.max(320, w)) }),
      setPaletteOpen: (paletteOpen) => set({ paletteOpen }),
      setUploadOpen: (uploadOpen) => set({ uploadOpen }),
      setTraceTurnId: (traceTurnId) => set({ traceTurnId }),
      setScenario: (scenario) => set({ scenario }),
    }),
    {
      name: 'legallens.ui',
      partialize: (s) => ({
        sidebarOpen: s.sidebarOpen,
        evidenceOpen: s.evidenceOpen,
        sidebarWidth: s.sidebarWidth,
        evidenceWidth: s.evidenceWidth,
        scenario: s.scenario,
      }),
    },
  ),
)
