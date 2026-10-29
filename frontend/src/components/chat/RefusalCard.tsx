import { useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  FileUp,
  Layers,
  PencilLine,
  SearchX,
  Shuffle,
} from 'lucide-react'
import type { Refusal, RefusalSuggestion } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { Progress } from '@/components/ui/Feedback'

const ICON: Record<RefusalSuggestion['kind'], React.ReactNode> = {
  widen_scope: <Layers aria-hidden className="size-3.5" />,
  upload: <FileUp aria-hidden className="size-3.5" />,
  rephrase: <PencilLine aria-hidden className="size-3.5" />,
  switch_mode: <Shuffle aria-hidden className="size-3.5" />,
}

/**
 * FR-14 / TS-3, and nuance #2 — the single most consequential styling decision
 * in this UI.
 *
 * A correct refusal is a measured success (target ≥90%). It gets calm slate,
 * never red, never a toast, and it keeps its feedback bar — because "the
 * answer *is* in my documents" is the signal we most need to collect.
 */
export function RefusalCard({
  refusal,
  onSuggestion,
}: {
  refusal: Refusal
  onSuggestion?: (s: RefusalSuggestion) => void
}) {
  const [showQueries, setShowQueries] = useState(false)

  return (
    <section
      aria-label="Not answerable from your documents"
      className="rounded-card border border-refusal-line bg-refusal-bg"
    >
      <div className="flex items-start gap-3 px-4 pt-3.5">
        <span className="mt-px grid size-7 shrink-0 place-items-center rounded-full border border-refusal-line bg-raised">
          <SearchX aria-hidden className="size-4 text-refusal" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-ink">Not answerable from your documents</h3>
          <p className="mt-1.5 font-serif text-[0.9375rem] leading-relaxed text-ink-2">
            {refusal.message}
          </p>
        </div>
      </div>

      {/* Showing the queries that ran is what makes a refusal auditable rather
          than merely asserted. */}
      {refusal.lookedFor.length > 0 && (
        <div className="mx-4 mt-3 rounded-control border border-refusal-line bg-raised">
          <button
            type="button"
            onClick={() => setShowQueries((v) => !v)}
            aria-expanded={showQueries}
            className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left"
          >
            {showQueries ? (
              <ChevronDown aria-hidden className="size-3.5 text-ink-3" />
            ) : (
              <ChevronRight aria-hidden className="size-3.5 text-ink-3" />
            )}
            <span className="text-2xs font-medium text-ink-2">
              What was searched for ({refusal.lookedFor.length} queries)
            </span>
          </button>
          {showQueries && (
            <ul className="space-y-1 border-t border-line px-2.5 py-2">
              {refusal.lookedFor.map((q) => (
                <li key={q} className="font-mono text-[11px] leading-relaxed text-ink-2">
                  <span className="text-ink-3">→</span> {q}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {refusal.bestRelevance !== undefined && refusal.threshold !== undefined && (
        <div className="mx-4 mt-3 flex items-center gap-3">
          <div className="min-w-0 flex-1">
            <Progress
              value={(refusal.bestRelevance / refusal.threshold) * 100}
              tone="caution"
              label="Best match relevance against the floor"
            />
          </div>
          <p className="shrink-0 font-mono text-[10px] text-ink-3">
            best {refusal.bestRelevance.toFixed(2)} · floor {refusal.threshold.toFixed(2)}
          </p>
        </div>
      )}

      {refusal.suggestions.length > 0 && (
        <div className="mt-3.5 border-t border-refusal-line px-4 py-3">
          <p className="text-2xs font-semibold uppercase tracking-wider text-ink-3">
            What you can do
          </p>
          <div className="mt-2 flex flex-col gap-1.5">
            {refusal.suggestions.map((s) => (
              <div key={s.kind} className="flex items-start gap-2">
                <Button
                  size="xs"
                  variant="secondary"
                  className="mt-px shrink-0"
                  onClick={() => onSuggestion?.(s)}
                >
                  {ICON[s.kind]}
                  {s.label}
                </Button>
                {s.detail && <p className="pt-1 text-2xs leading-relaxed text-ink-3">{s.detail}</p>}
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
