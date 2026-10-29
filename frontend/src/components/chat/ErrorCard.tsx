import { useEffect, useState } from 'react'
import { CloudOff, FileWarning, Gauge, RefreshCw, TimerOff, WifiOff, Eye } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { ApiError } from '@/api/types'
import { Button } from '@/components/ui/Button'

const ICON: Record<ApiError['kind'], React.ReactNode> = {
  llm_unavailable: <CloudOff aria-hidden className="size-4" />,
  rate_limited: <Gauge aria-hidden className="size-4" />,
  parse_failed: <FileWarning aria-hidden className="size-4" />,
  timeout: <TimerOff aria-hidden className="size-4" />,
  network: <WifiOff aria-hidden className="size-4" />,
}

const TITLE: Record<ApiError['kind'], string> = {
  llm_unavailable: 'Generation unavailable',
  rate_limited: 'Rate limited',
  parse_failed: 'Document could not be read',
  timeout: 'Generation timed out',
  network: 'Connection lost',
}

/**
 * FR-28: every variant names a next action.
 *
 * NFR-6 is the interesting one — when the provider is down, retrieval has
 * already run and the excerpts are real, so the card offers them. The product
 * stays useful during an outage instead of becoming a dead end.
 */
export function ErrorCard({
  error,
  onRetry,
  onShowExcerpts,
}: {
  error: ApiError
  onRetry?: () => void
  onShowExcerpts?: () => void
}) {
  const [remaining, setRemaining] = useState(error.retryAfterSec ?? 0)

  useEffect(() => {
    if (!error.retryAfterSec) return
    setRemaining(error.retryAfterSec)
    const id = window.setInterval(() => setRemaining((r) => Math.max(0, r - 1)), 1000)
    return () => window.clearInterval(id)
  }, [error.retryAfterSec])

  const waiting = remaining > 0

  return (
    <section
      aria-label={TITLE[error.kind]}
      className="rounded-card border border-danger-line bg-danger-bg"
    >
      <div className="flex items-start gap-3 p-3.5">
        <span className="mt-px grid size-7 shrink-0 place-items-center rounded-full border border-danger-line bg-raised text-danger">
          {ICON[error.kind]}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-ink">{TITLE[error.kind]}</h3>
          <p className="mt-1 text-xs leading-relaxed text-ink-2">{error.message}</p>
          <p className="mt-2 text-xs font-medium leading-relaxed text-ink">{error.action}</p>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {onRetry && (
              <Button size="xs" variant="secondary" onClick={onRetry} disabled={waiting}>
                <RefreshCw aria-hidden className="size-3.5" />
                {waiting ? `Retry in ${remaining}s` : 'Retry'}
              </Button>
            )}
            {error.excerptsAvailable && onShowExcerpts && (
              <Button size="xs" variant="secondary" onClick={onShowExcerpts}>
                <Eye aria-hidden className="size-3.5" />
                Read the retrieved passages
              </Button>
            )}
            {error.documentId && (
              <Button size="xs" variant="ghost" asChild>
                <Link to={`/doc/${error.documentId}`}>Open the document</Link>
              </Button>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}

/** NFR-7: a partial answer is never allowed to read as a complete one. */
export function IncompleteBanner() {
  return (
    <p
      role="status"
      className="mt-3 flex items-center gap-2 rounded-control border border-caution-line bg-caution-bg px-2.5 py-1.5 text-2xs font-medium text-caution"
    >
      <TimerOff aria-hidden className="size-3.5 shrink-0" />
      Incomplete — the response stream was interrupted. Everything above is partial and is marked
      as such in any copy or export.
    </p>
  )
}
