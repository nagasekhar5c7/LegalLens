import { useState } from 'react'
import { Check, ChevronDown, ChevronRight } from 'lucide-react'
import type { Stage } from '@/api/types'
import { cn } from '@/lib/cn'
import { formatMs } from '@/lib/format'
import { Spinner } from '@/components/ui/Feedback'

/**
 * Nuance #6: a stage trace, not a spinner.
 *
 * A 200-page summary is budgeted at three minutes (NFR-3). A generic spinner
 * for three minutes reads as a hang; naming the stage makes the wait legible.
 * Once the turn completes this collapses to one line and gets out of the way.
 */
export function StageTrace({
  stages,
  streaming,
  className,
}: {
  stages: Stage[]
  streaming: boolean
  className?: string
}) {
  const [open, setOpen] = useState(false)
  if (stages.length === 0) return null

  const total = Math.max(...stages.map((s) => s.elapsedMs))
  const active = stages.find((s) => s.status === 'active')

  if (streaming && !open) {
    return (
      <div className={cn('flex items-center gap-2 text-xs text-ink-2', className)}>
        <Spinner className="size-3 text-navy-600" />
        <span className="font-medium">{active?.label ?? 'Working'}</span>
        {active?.detail && <span className="truncate text-ink-3">· {active.detail}</span>}
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="ml-auto shrink-0 rounded-[4px] px-1 py-0.5 text-2xs text-ink-3 hover:bg-hover hover:text-ink"
        >
          Show steps
        </button>
      </div>
    )
  }

  return (
    <div className={cn('rounded-card border border-line bg-canvas print:hidden', className)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left"
      >
        {open ? (
          <ChevronDown aria-hidden className="size-3.5 shrink-0 text-ink-3" />
        ) : (
          <ChevronRight aria-hidden className="size-3.5 shrink-0 text-ink-3" />
        )}
        {streaming ? (
          <Spinner className="size-3 text-navy-600" />
        ) : (
          <Check aria-hidden className="size-3.5 shrink-0 text-grounded" />
        )}
        <span className="text-2xs font-medium text-ink-2">
          {streaming ? (active?.label ?? 'Working') : `${stages.length} steps`}
        </span>
        <span className="ml-auto font-mono text-[10px] text-ink-3">{formatMs(total)}</span>
      </button>

      {open && (
        <ol className="border-t border-line px-2.5 py-1.5">
          {stages.map((s, i) => (
            <li key={`${s.key}-${i}`} className="flex items-baseline gap-2 py-1">
              <span className="w-3 shrink-0">
                {s.status === 'done' ? (
                  <Check aria-hidden className="size-3 text-grounded" />
                ) : (
                  <Spinner className="size-2.5 text-navy-600" />
                )}
              </span>
              <span className="shrink-0 text-2xs font-medium text-ink">{s.label}</span>
              {s.detail && <span className="truncate text-2xs text-ink-3">{s.detail}</span>}
              <span className="ml-auto shrink-0 font-mono text-[10px] text-ink-3">
                {formatMs(s.elapsedMs)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
