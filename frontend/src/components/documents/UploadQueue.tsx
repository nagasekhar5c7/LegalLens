import { AlertCircle, Check, FileText, Loader, X } from 'lucide-react'
import { cn } from '@/lib/cn'
import { formatBytes, shortDocName } from '@/lib/format'
import { IconButton } from '@/components/ui/Button'
import { Progress } from '@/components/ui/Feedback'
import type { UploadRow } from '@/store/useWorkspace'

/**
 * FR-3: one row per file, each with its own state. A rejected or failed file
 * sits next to the successful ones rather than aborting the batch — which is
 * the difference between "upload failed" and "six of seven are ready".
 */
export function UploadQueue({
  rows,
  onClear,
}: {
  rows: UploadRow[]
  onClear: () => void
}) {
  if (rows.length === 0) return null

  const done = rows.filter((r) => r.state === 'done').length
  const bad = rows.filter((r) => r.state === 'rejected' || r.state === 'failed').length
  const busy = rows.some((r) => r.state === 'ingesting' || r.state === 'validating')

  return (
    <section aria-label="Upload queue" className="rounded-card border border-line bg-raised">
      <header className="flex items-center gap-2 border-b border-line px-2.5 py-1.5">
        <h3 className="min-w-0 flex-1 text-2xs font-semibold text-ink">
          {busy ? 'Processing' : 'Processed'} {rows.length} file{rows.length === 1 ? '' : 's'}
        </h3>
        {done > 0 && <span className="shrink-0 text-[10px] text-grounded">{done} ready</span>}
        {bad > 0 && <span className="shrink-0 text-[10px] text-danger">{bad} rejected</span>}
        {!busy && (
          <IconButton label="Clear the upload list" size="xs" variant="quiet" onClick={onClear}>
            <X className="size-3.5" />
          </IconButton>
        )}
      </header>

      <ul className="divide-y divide-line">
        {rows.map((r) => (
          <li key={r.key} className="flex items-start gap-2.5 px-2.5 py-2">
            <span
              className={cn(
                'mt-px grid size-5 shrink-0 place-items-center rounded-[5px]',
                r.state === 'done'
                  ? 'bg-grounded-bg text-grounded'
                  : r.state === 'rejected' || r.state === 'failed'
                    ? 'bg-danger-bg text-danger'
                    : 'bg-navy-50 text-navy-600',
              )}
            >
              {r.state === 'done' ? (
                <Check aria-hidden className="size-3" />
              ) : r.state === 'rejected' || r.state === 'failed' ? (
                <AlertCircle aria-hidden className="size-3" />
              ) : r.state === 'ingesting' ? (
                <Loader aria-hidden className="size-3 animate-[ll-spin_1.4s_linear_infinite]" />
              ) : (
                <FileText aria-hidden className="size-3" />
              )}
            </span>

            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-2">
                <p className="min-w-0 flex-1 truncate text-xs text-ink">
                  {shortDocName(r.name, 40)}
                </p>
                <span className="shrink-0 text-[10px] text-ink-3">{formatBytes(r.sizeBytes)}</span>
              </div>

              {r.reason ? (
                <p className="mt-0.5 text-2xs leading-relaxed text-danger">{r.reason}</p>
              ) : r.state === 'ingesting' ? (
                <Progress value={r.progress} className="mt-1.5" label={`Ingesting ${r.name}`} />
              ) : r.state === 'done' ? (
                <p className="mt-0.5 text-2xs text-grounded">Indexed and searchable</p>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
