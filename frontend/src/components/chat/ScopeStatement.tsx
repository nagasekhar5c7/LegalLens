import { AlertTriangle, Layers } from 'lucide-react'
import type { ScopeUsed } from '@/api/types'
import { cn } from '@/lib/cn'
import { pluralise, shortDocName } from '@/lib/format'
import { Tooltip } from '@/components/ui/Floating'

/**
 * FR-9, and nuance #5: the scope a question actually searched is stated on the
 * response, not buried in a tooltip. Workspace-wide Ask loses precision as the
 * corpus grows; naming the scope is the stated mitigation.
 */
export function ScopeStatement({ scope, className }: { scope: ScopeUsed; className?: string }) {
  const where =
    scope.kind === 'workspace'
      ? 'whole workspace'
      : scope.kind === 'document'
        ? 'one document'
        : `${scope.documentCount} selected`

  return (
    <span className={cn('inline-flex items-center gap-1.5 text-2xs text-ink-3', className)}>
      <Layers aria-hidden className="size-3 shrink-0" />
      <span>
        Searched <span className="font-medium text-ink-2">{pluralise(scope.documentCount, 'document')}</span>
        {' · '}
        {scope.pageCount.toLocaleString()} pages
        {' · '}
        {where}
      </span>

      {scope.excluded.length > 0 && (
        <Tooltip
          side="bottom"
          content={
            <span className="block">
              <span className="font-semibold">Excluded from this search:</span>
              {scope.excluded.map((e) => (
                <span key={e.documentId} className="mt-1 block">
                  {shortDocName(e.name, 38)} — {e.reason}
                </span>
              ))}
            </span>
          }
        >
          <span className="inline-flex cursor-help items-center gap-1 rounded-full bg-caution-bg px-1.5 py-px text-caution">
            <AlertTriangle aria-hidden className="size-2.5" />
            {scope.excluded.length} excluded
          </span>
        </Tooltip>
      )}
    </span>
  )
}
