import { useNavigate } from 'react-router-dom'
import { ArrowUpRight, FileText } from 'lucide-react'
import type { Citation, Excerpt } from '@/api/types'
import { cn } from '@/lib/cn'
import { citationLabel, shortDocName } from '@/lib/format'
import { useEvidence } from '@/store/useEvidence'
import { useUi } from '@/store/useUi'
import { HoverCard, HoverCardContent, HoverCardTrigger } from '@/components/ui/Floating'
import { RelevanceMeter } from '@/components/evidence/RelevanceMeter'

/**
 * FR-11 / FR-13 / NFR-13.
 *
 * Hover gives a peek; click opens the evidence panel and selects the span;
 * ⌥↵ on a focused chip opens the document at the highlighted span. The SR
 * label names the document and page, because "1" on its own tells a
 * screen-reader user nothing.
 */
export function CitationChip({
  citation,
  excerpt,
  className,
}: {
  citation: Citation
  excerpt?: Excerpt
  className?: string
}) {
  const navigate = useNavigate()
  const { activeSpanId, setActive } = useEvidence()
  const setEvidenceOpen = useUi((s) => s.setEvidenceOpen)
  const active = activeSpanId === citation.spanId

  function select() {
    setActive(citation.spanId)
    setEvidenceOpen(true)
    document
      .querySelector(`[data-excerpt="${citation.spanId}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  function openInDocument() {
    navigate(`/doc/${citation.documentId}?page=${citation.page}&span=${citation.spanId}`)
  }

  return (
    <HoverCard openDelay={180} closeDelay={80}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          data-citation={citation.n}
          onClick={select}
          onKeyDown={(e) => {
            if (e.altKey && e.key === 'Enter') {
              e.preventDefault()
              openInDocument()
            }
          }}
          aria-label={`Citation ${citation.n}: ${shortDocName(citation.documentName, 60)}${
            citation.section ? `, ${citation.section}` : ''
          }, page ${citation.page}`}
          className={cn(
            'mx-px inline-flex h-[1.15em] min-w-[1.3em] shrink-0 translate-y-[-0.1em] items-center justify-center',
            'rounded-[4px] border px-[0.28em] align-middle font-sans text-[0.72em] font-semibold leading-none',
            'transition-colors duration-100',
            active
              ? 'border-navy-600 bg-navy-600 text-ink-inverse'
              : 'border-navy-200 bg-navy-50 text-navy-700 hover:border-navy-500 hover:bg-navy-100',
            className,
          )}
        >
          {citation.n}
        </button>
      </HoverCardTrigger>

      <HoverCardContent>
        <div className="flex items-start gap-2 border-b border-line px-3 py-2">
          <FileText aria-hidden className="mt-px size-3.5 shrink-0 text-ink-3" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-2xs font-semibold text-ink">
              {shortDocName(citation.documentName, 44)}
            </p>
            <p className="font-mono text-[10px] text-ink-3">{citationLabel(citation)}</p>
          </div>
          {excerpt && <RelevanceMeter value={excerpt.relevance} />}
        </div>

        {excerpt ? (
          <p className="max-h-32 overflow-hidden px-3 py-2.5 font-serif text-xs leading-relaxed text-ink-2">
            <mark className="bg-highlight text-ink">{excerpt.text.slice(0, 260)}</mark>
            {excerpt.text.length > 260 && '…'}
          </p>
        ) : (
          <p className="px-3 py-2.5 text-2xs italic text-ink-3">
            Excerpt still arriving from the stream.
          </p>
        )}

        <div className="flex items-center justify-between gap-2 border-t border-line bg-canvas px-3 py-1.5">
          <span className="text-[10px] text-ink-3">Click to verify beside the answer</span>
          <button
            type="button"
            onClick={openInDocument}
            className="inline-flex items-center gap-1 rounded-[4px] px-1 py-0.5 text-[10px] font-semibold text-navy-600 hover:bg-navy-50"
          >
            Open document
            <ArrowUpRight aria-hidden className="size-3" />
          </button>
        </div>
      </HoverCardContent>
    </HoverCard>
  )
}
