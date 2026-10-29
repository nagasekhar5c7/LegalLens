import { create } from 'zustand'
import type { ServerConfig } from '@/api/types'
import { api } from '@/api/client'

/**
 * Server-enforced limits, read once at boot.
 *
 * FR-2 requires the rejection message to name the *real* limit. Keeping a
 * second copy of these numbers in the client is the kind of thing that is
 * correct right up until someone sets `LL_MAX_UPLOAD_BYTES` and the dropzone
 * starts refusing files the server would have taken.
 *
 * `scenariosEnabled` rides along in the same payload: when the backend reports
 * `false` (`LL_ENV=production`) the Demo menu hides itself rather than sending
 * a header the server ignores.
 */

/**
 * What the server said before it was asked. Deliberately conservative and
 * matching the backend's own defaults: a failed config fetch should degrade to
 * a working dropzone, not a broken one.
 */
export const FALLBACK_CONFIG: ServerConfig = {
  maxBytes: 50 * 1024 * 1024,
  maxPages: 500,
  maxDocuments: 50,
  acceptedExtensions: ['.pdf', '.docx', '.txt'],
  scenariosEnabled: false,
  generationBudgetS: 60,
}

interface ConfigState {
  config: ServerConfig
  loaded: boolean
  load: () => Promise<void>
}

export const useConfig = create<ConfigState>((set, get) => ({
  config: FALLBACK_CONFIG,
  loaded: false,

  load: async () => {
    if (get().loaded) return
    try {
      set({ config: await api.getConfig(), loaded: true })
    } catch {
      // Keep the fallback. `loaded` stays false so a later mount retries.
    }
  },
}))
