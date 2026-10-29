import { Link } from 'react-router-dom'
import { AlertCircle, ChevronRight, Database, Loader } from 'lucide-react'
import type { WorkspaceHealth } from '@/api/types'
import { pluralise } from '@/lib/format'

/**
 * US-2 in one tile: what is in the workspace and whether it can be queried.
 *
 * "6 documents" is not the useful number — "4 ready" is, because only `ready`
 * documents are searchable (FR-7). Failures and in-flight work are called out
 * rather than averaged away.
 */
export function WorkspaceHealthCard({ health }: { health: WorkspaceHealth }) {
  const allReady = health.failed === 0 && health.indexing === 0

  return (
    <Link
      to="/library"
      className="group block rounded-card border border-line bg-raised px-2.5 py-2 shadow-sm transition-colors hover:border-line-strong hover:bg-hover"
    >
      <div className="flex items-center gap-2">
        <Database aria-hidden className="size-3.5 shrink-0 text-ink-3" />
        <p className="min-w-0 flex-1 text-2xs font-semibold text-ink">
          {health.documents === 0 ? 'No documents yet' : pluralise(health.documents, 'document')}
        </p>
        <ChevronRight
          aria-hidden
          className="size-3.5 shrink-0 text-ink-3 transition-transform group-hover:translate-x-0.5"
        />
      </div>

      {health.documents > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px]">
          <span className="inline-flex items-center gap-1 text-grounded">
            <span aria-hidden className="size-1.5 rounded-full bg-grounded" />
            {health.ready} searchable
          </span>
          {health.indexing > 0 && (
            <span className="inline-flex items-center gap-1 text-navy-600">
              <Loader aria-hidden className="size-2.5 animate-[ll-spin_1.6s_linear_infinite]" />
              {health.indexing} indexing
            </span>
          )}
          {health.failed > 0 && (
            <span className="inline-flex items-center gap-1 text-danger">
              <AlertCircle aria-hidden className="size-2.5" />
              {health.failed} failed
            </span>
          )}
          {allReady && (
            <span className="text-ink-3">· {health.pages.toLocaleString()} pages indexed</span>
          )}
        </div>
      )}
    </Link>
  )
}
