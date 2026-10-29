import { ArrowUpRight, Copy } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import type { Citation, Excerpt } from '@/api/types'
import { cn } from '@/lib/cn'
import { citationLabel } from '@/lib/format'
import { copyText } from '@/lib/exporting'
import { toast } from '@/store/useToasts'
import { IconButton } from '@/components/ui/Button'
import { Tooltip } from '@/components/ui/Floating'
import { RelevanceMeter } from './RelevanceMeter'

/**
 * FR-12: the retrieved span, in its surrounding context, beside the answer.
 *
 * Context matters as much as the span — a clause read without the sentences
 * around it is how a correct quotation becomes a wrong conclusion.
 */
export function ExcerptCard({
  excerpt,
  citation,
  active,
  onSelect,
}: {
  excerpt: Excerpt
  citation?: Citation
  active: boolean
  onSelect: () => void
}) {
  const navigate = useNavigate()

  async function copyWithCitation() {
    const label = citation ? citationLabel(citation) : `${excerpt.documentName}, p. ${excerpt.page}`
    const ok = await copyText(`“${excerpt.text}”\n\n— ${label} (${excerpt.spanId})`)
    if (ok) toast.done('Excerpt copied with its citation')
  }

  return (
    <article
      data-excerpt={excerpt.spanId}
      onClick={onSelect}
      className={cn(
        'scroll-mt-4 rounded-card border bg-raised transition-[border-color,box-shadow]',
        active ? 'border-navy-600 shadow-sm ring-1 ring-navy-600/20' : 'border-line hover:border-line-strong',
      )}
    >
      <header className="flex items-center gap-2 border-b border-line px-2.5 py-1.5">
        {citation && (
          <span
            className={cn(
              'grid size-4.5 shrink-0 place-items-center rounded-[4px] text-2xs font-semibold',
              active ? 'bg-navy-600 text-ink-inverse' : 'bg-navy-50 text-navy-700',
            )}
          >
            {citation.n}
          </span>
        )}
        <p className="min-w-0 flex-1 truncate font-mono text-[10px] text-ink-2">
          {citation ? citationLabel(citation) : `p. ${excerpt.page}`}
        </p>
        <RelevanceMeter value={excerpt.relevance} />
      </header>

      <blockquote className="px-2.5 py-2 font-serif text-xs leading-relaxed text-ink-3">
        {excerpt.before && <span>{excerpt.before} </span>}
        <mark
          className={cn(
            'rounded-[2px] px-px text-ink',
            active ? 'bg-highlight-strong' : 'bg-highlight',
          )}
        >
          {excerpt.text}
        </mark>
        {excerpt.after && <span> {excerpt.after}</span>}
      </blockquote>

      <footer className="flex items-center justify-between gap-1 border-t border-line px-1.5 py-1">
        <span className="truncate pl-1 text-[10px] text-ink-3">{excerpt.section ?? 'Retrieved span'}</span>
        <span className="flex shrink-0 items-center">
          <Tooltip content="Copy the excerpt with its citation">
            <IconButton
              label="Copy excerpt with citation"
              size="xs"
              variant="quiet"
              onClick={(e) => {
                e.stopPropagation()
                void copyWithCitation()
              }}
            >
              <Copy className="size-3" />
            </IconButton>
          </Tooltip>
          <Tooltip content="Open the document at this span">
            <IconButton
              label="Open the document at this span"
              size="xs"
              variant="quiet"
              onClick={(e) => {
                e.stopPropagation()
                navigate(`/doc/${excerpt.documentId}?page=${excerpt.page}&span=${excerpt.spanId}`)
              }}
            >
              <ArrowUpRight className="size-3" />
            </IconButton>
          </Tooltip>
        </span>
      </footer>
    </article>
  )
}
