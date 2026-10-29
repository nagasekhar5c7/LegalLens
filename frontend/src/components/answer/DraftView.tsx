import { useState } from 'react'
import { ListTree } from 'lucide-react'
import type { Citation, DraftPayload, Excerpt } from '@/api/types'
import { cn } from '@/lib/cn'
import { Badge } from '@/components/ui/Badge'
import { Skeleton } from '@/components/ui/Feedback'
import { Markdown } from '@/components/chat/Markdown'
import { DraftBanner } from './DraftBanner'
import { DRAFT_SECTIONS, DRAFT_TYPE_LABEL } from '@/lib/templates'

/**
 * FR-18: memo as Question Presented → Short Answer → Statement of Facts →
 * Discussion → Conclusion; case brief in its own conventional order. The
 * structure is the point — an attorney should be able to edit this rather than
 * rewrite it.
 */
export function DraftView({
  draft,
  citations,
  excerpts,
  streaming,
}: {
  draft: DraftPayload
  citations: Citation[]
  excerpts: Excerpt[]
  streaming?: boolean
}) {
  const [active, setActive] = useState<string | null>(null)
  const expected = DRAFT_SECTIONS[draft.draftType]
  const pending = expected.slice(draft.sections.length)

  return (
    <article className="overflow-hidden rounded-card border border-draft-line bg-raised">
      <DraftBanner draftType={draft.draftType} />

      <div className="px-4 py-3.5">
        <header className="border-b border-line pb-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="draft">{DRAFT_TYPE_LABEL[draft.draftType]}</Badge>
            {draft.params.audience && (
              <Badge tone="neutral">For: {draft.params.audience}</Badge>
            )}
            {draft.params.tone && <Badge tone="neutral">{draft.params.tone}</Badge>}
          </div>
          <h2 className="mt-2 font-serif text-base font-semibold leading-snug text-ink">
            {draft.title}
          </h2>
          {draft.params.position && (
            <p className="mt-1.5 text-2xs text-ink-2">
              <span className="font-semibold text-ink-3">Position argued:</span>{' '}
              {draft.params.position}
            </p>
          )}
        </header>

        {/* Outline nav — a memo is read by section, not top to bottom. */}
        {expected.length > 2 && (
          <nav aria-label="Draft outline" className="mt-3 flex flex-wrap items-center gap-1.5">
            <ListTree aria-hidden className="size-3.5 shrink-0 text-ink-3" />
            {expected.map((s) => {
              const done = draft.sections.some((x) => x.key === s.key)
              return (
                <button
                  key={s.key}
                  type="button"
                  disabled={!done}
                  onClick={() => {
                    setActive(s.key)
                    document
                      .getElementById(`draft-${draft.draftType}-${s.key}`)
                      ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                  }}
                  className={cn(
                    'rounded-full border px-2 py-0.5 text-2xs font-medium transition-colors',
                    !done
                      ? 'border-line bg-sunken text-ink-3'
                      : active === s.key
                        ? 'border-navy-600 bg-navy-50 text-navy-700'
                        : 'border-line bg-raised text-ink-2 hover:bg-hover',
                  )}
                >
                  {s.heading}
                </button>
              )
            })}
          </nav>
        )}

        <div className="mt-1">
          {draft.sections.map((section) => (
            <section
              key={section.key}
              id={`draft-${draft.draftType}-${section.key}`}
              className="scroll-mt-20 border-t border-line py-3.5"
            >
              <h3 className="text-2xs font-semibold uppercase tracking-[0.1em] text-ink-3">
                {section.heading}
              </h3>
              <div className="mt-2">
                <Markdown md={section.bodyMd} citations={citations} excerpts={excerpts} />
              </div>
            </section>
          ))}

          {streaming &&
            pending.map((s) => (
              <div key={s.key} className="border-t border-line py-3.5">
                <h3 className="text-2xs font-semibold uppercase tracking-[0.1em] text-ink-3/60">
                  {s.heading}
                </h3>
                <Skeleton className="mt-2 h-3 w-full" />
                <Skeleton className="mt-1.5 h-3 w-11/12" />
                <Skeleton className="mt-1.5 h-3 w-3/5" />
              </div>
            ))}
        </div>
      </div>
    </article>
  )
}
