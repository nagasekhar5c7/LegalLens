import { useEffect } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { Spinner } from '@/components/ui/Feedback'
import { useSession } from '@/store/useSession'
import { useConfig } from '@/store/useConfig'

/**
 * Every route but `/login` sits behind this.
 *
 * The three-state `status` matters: redirecting while the probe is still in
 * flight would bounce every cold load through `/login` and back, which reads as
 * a flicker and loses the deep link. `unknown` renders the same full-page
 * spinner the lazy-route fallback uses, so a cold load looks like one wait
 * rather than two.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const status = useSession((s) => s.status)
  const bootstrap = useSession((s) => s.bootstrap)
  const loadConfig = useConfig((s) => s.load)
  const location = useLocation()

  useEffect(() => {
    void bootstrap()
    // Public endpoint, so it can run alongside the session probe rather than
    // after it — the dropzone's limits are ready before the library mounts.
    void loadConfig()
  }, [bootstrap, loadConfig])

  if (status === 'unknown') {
    return (
      <div className="grid min-h-dvh place-items-center bg-canvas" role="status" aria-live="polite">
        <span className="flex items-center gap-2 text-xs text-ink-3">
          <Spinner className="size-4 text-navy-600" />
          Loading…
        </span>
      </div>
    )
  }

  if (status === 'anon') {
    // `from` survives the round trip, so signing in returns to the deep link
    // the user actually opened.
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }

  return children
}
