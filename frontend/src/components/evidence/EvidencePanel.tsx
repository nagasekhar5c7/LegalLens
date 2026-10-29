import { useMemo } from 'react'
import { PanelRightClose, Quote, ShieldCheck } from 'lucide-react'
import { cn } from '@/lib/cn'
import { pluralise, shortDocName } from '@/lib/format'
import { IconButton } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/Feedback'
import { Tooltip } from '@/components/ui/Floating'
import { useEvidence } from '@/store/useEvidence'
import { useUi } from '@/store/useUi'
import { ExcerptCard } from './ExcerptCard'

/**
 * Nuance #1: evidence is a pane, not a popover.
 *
 * FR-12 requires excerpts to be visible with no further user action, and BR-4
 * requires verification to cost seconds. Both point at the same layout: the
 * retrieved text sits beside the answer the whole time, filling as the stream
 * delivers it.
 */
export function EvidencePanel({ className }: { className?: string }) {
  const { citations, excerpts, activeSpanId, setActive } = useEvidence()
  const toggleEvidence = useUi((s) => s.toggleEvidence)

  const groups = useMemo(() => {
    const byDoc = new Map<string, { name: string; items: typeof excerpts }>()
    for (const e of excerpts) {
      const g = byDoc.get(e.documentId) ?? { name: e.documentName, items: [] }
      g.items.push(e)
      byDoc.set(e.documentId, g)
    }
    for (const g of byDoc.values()) g.items.sort((a, b) => b.relevance - a.relevance)
    return [...byDoc.entries()]
  }, [excerpts])

  return (
    <aside
      aria-label="Evidence — retrieved passages"
      className={cn('flex min-h-0 flex-col border-l border-line bg-canvas print:hidden', className)}
    >
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-3">
        <Quote aria-hidden className="size-3.5 shrink-0 text-ink-3" />
        <h2 className="min-w-0 flex-1 text-xs font-semibold text-ink">Evidence</h2>
        {excerpts.length > 0 && (
          <span className="shrink-0 text-2xs text-ink-3">{pluralise(excerpts.length, 'passage')}</span>
        )}
        <Tooltip content="Hide the evidence panel" shortcut="]">
          <IconButton label="Hide the evidence panel" size="xs" variant="quiet" onClick={toggleEvidence}>
            <PanelRightClose className="size-4" />
          </IconButton>
        </Tooltip>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {excerpts.length === 0 ? (
          <EmptyState
            icon={<ShieldCheck className="size-4" />}
            title="Nothing to verify yet"
            body="Retrieved passages appear here as an answer streams, so every claim can be checked against its source without leaving the page."
          />
        ) : (
          <div className="space-y-4 p-2.5">
            {groups.map(([docId, group]) => (
              <section key={docId}>
                <h3 className="mb-1.5 flex items-baseline gap-1.5 px-0.5">
                  <span className="min-w-0 flex-1 truncate text-2xs font-semibold text-ink-2">
                    {shortDocName(group.name, 34)}
                  </span>
                  <span className="shrink-0 text-[10px] text-ink-3">{group.items.length}</span>
                </h3>
                <div className="space-y-2">
                  {group.items.map((e) => (
                    <ExcerptCard
                      key={e.spanId}
                      excerpt={e}
                      citation={citations.find((c) => c.spanId === e.spanId)}
                      active={activeSpanId === e.spanId}
                      onSelect={() => setActive(e.spanId)}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>

      {excerpts.length > 0 && (
        <footer className="shrink-0 border-t border-line px-3 py-2">
          <p className="flex items-start gap-1.5 text-[10px] leading-relaxed text-ink-3">
            <ShieldCheck aria-hidden className="mt-px size-3 shrink-0 text-grounded" />
            Every passage shown was actually retrieved and is what the answer was written from.
            Nothing here comes from model knowledge.
          </p>
        </footer>
      )}
    </aside>
  )
}
