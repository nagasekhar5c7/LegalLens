import { create } from 'zustand'
import type { Me } from '@/api/types'
import { api } from '@/api/client'
import { setUnauthorizedHandler } from '@/api/http'

/**
 * FR-27: one user, a server-side session, an `HttpOnly` cookie.
 *
 * Nothing here holds a token — the cookie is unreadable from JavaScript by
 * design, which is the point of server-side sessions in a product that carries
 * client confidences. "Am I signed in?" is answered by asking the server once
 * (`GET /api/auth/me`) and then by watching for a 401.
 */
type SessionStatus = 'unknown' | 'authed' | 'anon'

interface SessionState {
  status: SessionStatus
  me: Me | null
  /**
   * Why the session ended, when it ended mid-use. The login screen says so
   * rather than presenting an empty form and letting the user wonder.
   */
  lapsed: string | null
  bootstrap: () => Promise<void>
  signIn: (email: string, password: string) => Promise<{ ok: boolean; error?: string }>
  signOut: () => Promise<void>
}

export const useSession = create<SessionState>((set, get) => ({
  status: 'unknown',
  me: null,
  lapsed: null,

  /**
   * Called once by the route guard before the shell renders. Idempotent: a
   * second call while already resolved is a no-op, so remounting a guarded
   * route does not re-probe.
   */
  bootstrap: async () => {
    if (get().status !== 'unknown') return
    try {
      const me = await api.me()
      set(me ? { status: 'authed', me, lapsed: null } : { status: 'anon', me: null })
    } catch {
      // A backend that is down is not a signed-out user, but there is nothing
      // useful to render either. The login screen is the honest landing place.
      set({ status: 'anon', me: null })
    }
  },

  signIn: async (email, password) => {
    const res = await api.login(email, password)
    if (!res.ok) return { ok: false, error: res.error }
    set({ status: 'authed', me: res.me, lapsed: null })
    return { ok: true }
  },

  signOut: async () => {
    try {
      await api.logout()
    } finally {
      // Local state clears even if the call failed: the user asked to be out.
      set({ status: 'anon', me: null, lapsed: null })
    }
  },
}))

/**
 * One expired session flips the whole app to signed-out, once.
 *
 * Registered at module scope rather than in a component, so a 401 on a call
 * made from anywhere — including one in flight while the user is on a route
 * that has already unmounted — lands here instead of surfacing as seven
 * identical "network error" toasts.
 */
setUnauthorizedHandler((error) => {
  if (useSession.getState().status === 'anon') return
  useSession.setState({ status: 'anon', me: null, lapsed: error.message })
})
