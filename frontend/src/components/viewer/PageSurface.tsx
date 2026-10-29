import { useEffect, useRef } from 'react'
import { ScanLine } from 'lucide-react'
import type { Page, Span } from '@/api/types'
import { cn } from '@/lib/cn'

/**
 * One page. Phase 1 renders synthetic text rather than a PDF raster — PDF.js
 * arrives in M3 with real files. The important part is already real: the
 * highlight is computed from character offsets, so when the renderer changes
 * the span maths does not.
 */
export function PageSurface({
  page,
  span,
  active,
  zoom,
  height,
}: {
  page: Page | null
  span: Span | null
  active: boolean
  zoom: number
  height: number
}) {
  const markRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (!active || !markRef.current) return
    markRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
    markRef.current.style.animation = 'll-pulse 1.6s var(--ease-out-quint) 1'
  }, [active, page])

  return (
    <article
      aria-label={`Page ${page?.page ?? '—'}`}
      style={{ minHeight: height }}
      className={cn(
        'relative mx-auto w-full max-w-[52rem] rounded-card border bg-raised shadow-sm',
        active ? 'border-navy-600 ring-1 ring-navy-600/20' : 'border-line',
      )}
    >
      <header className="flex items-center gap-2 border-b border-line px-4 py-1.5">
        <span className="font-mono text-[10px] text-ink-3">
          Page {page?.page ?? '—'}
        </span>
        {page?.ocrConfidence !== undefined && (
          <span className="inline-flex items-center gap-1 rounded-full bg-caution-bg px-1.5 py-px text-[10px] font-medium text-caution">
            <ScanLine aria-hidden className="size-2.5" />
            OCR {Math.round(page.ocrConfidence * 100)}% — below the floor
          </span>
        )}
      </header>

      <div
        className="px-10 py-8 font-serif text-ink"
        style={{ fontSize: `${0.875 * zoom}rem`, lineHeight: 1.85 }}
      >
        {page ? (
          <PageText text={page.text} span={span} markRef={markRef} />
        ) : (
          <p className="text-ink-3">Loading…</p>
        )}
      </div>
    </article>
  )
}

function PageText({
  text,
  span,
  markRef,
}: {
  text: string
  span: Span | null
  markRef: React.RefObject<HTMLElement | null>
}) {
  if (!span) {
    return (
      <>
        {text.split('\n\n').map((p, i) => (
          <p key={i} className={i > 0 ? 'mt-4' : undefined}>
            {p}
          </p>
        ))}
      </>
    )
  }

  /* FR-13: highlight the exact character range, not the whole paragraph. */
  const before = text.slice(0, span.charStart)
  const hit = text.slice(span.charStart, span.charEnd)
  const after = text.slice(span.charEnd)

  const render = (chunk: string, keyBase: string, offset: number) =>
    chunk.split('\n\n').map((p, i) => (
      <p key={`${keyBase}-${i}`} className={offset + i > 0 ? 'mt-4' : undefined}>
        {p}
      </p>
    ))

  const beforeParas = before.split('\n\n')
  const lastBefore = beforeParas.pop() ?? ''
  const afterParas = after.split('\n\n')
  const firstAfter = afterParas.shift() ?? ''

  return (
    <>
      {render(beforeParas.join('\n\n'), 'b', 0)}
      <p className={beforeParas.length > 0 ? 'mt-4' : undefined}>
        {lastBefore}
        <mark
          ref={markRef}
          className="rounded-[2px] bg-highlight px-px text-ink"
          data-span={span.id}
        >
          {hit}
        </mark>
        {firstAfter}
      </p>
      {render(afterParas.join('\n\n'), 'a', 1)}
    </>
  )
}
