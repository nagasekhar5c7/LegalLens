import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Minus, Plus } from 'lucide-react'
import type { LegalDocument } from '@/api/types'
import { usePages, useSpan } from '@/hooks/usePages'
import { Button, IconButton } from '@/components/ui/Button'
import { Input } from '@/components/ui/Field'
import { Tooltip } from '@/components/ui/Floating'
import { OcrWarningPanel } from '@/components/documents/OcrWarning'
import { PageSurface } from './PageSurface'

const BASE_PAGE_HEIGHT = 940
const WINDOW = 2

/**
 * Windowed page rendering.
 *
 * A 500-page document is the one real engineering risk here. Only pages near
 * the viewport are mounted; the rest reserve their height so the scrollbar
 * stays honest and a deep link to page 312 lands in the right place without
 * rendering 311 pages first. Page text itself comes from the server one page at
 * a time — see `usePages` for the cache and the abort-on-scroll-past.
 */
export function DocumentViewer({
  document: doc,
  initialPage,
  spanId,
}: {
  document: LegalDocument
  initialPage: number
  spanId?: string | null
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const [zoom, setZoom] = useState(1)
  const [current, setCurrent] = useState(initialPage)
  const [pageInput, setPageInput] = useState(String(initialPage))

  const pageHeight = BASE_PAGE_HEIGHT * zoom
  const span = useSpan(spanId)

  /* Jump to the deep-linked page on mount and whenever the target changes. */
  useEffect(() => {
    const el = scroller.current
    if (!el) return
    el.scrollTop = Math.max(0, (initialPage - 1) * (pageHeight + 16) - 24)
    setCurrent(initialPage)
    setPageInput(String(initialPage))
  }, [initialPage, doc.id, pageHeight])

  function onScroll() {
    const el = scroller.current
    if (!el) return
    const n = Math.min(doc.pages, Math.max(1, Math.round(el.scrollTop / (pageHeight + 16)) + 1))
    setCurrent(n)
    setPageInput(String(n))
  }

  function goTo(n: number) {
    const page = Math.min(doc.pages, Math.max(1, n))
    const el = scroller.current
    if (el) el.scrollTo({ top: (page - 1) * (pageHeight + 16), behavior: 'smooth' })
    setCurrent(page)
  }

  const first = Math.max(1, current - WINDOW)
  const last = Math.min(doc.pages, current + WINDOW)
  const visible = useMemo(() => {
    const ns: number[] = []
    for (let n = first; n <= last; n++) ns.push(n)
    return ns
  }, [first, last])
  const pages = usePages(doc.id, visible)

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line bg-canvas px-3 py-1.5">
        <Tooltip content="Previous page">
          <IconButton
            label="Previous page"
            size="xs"
            variant="quiet"
            disabled={current <= 1}
            onClick={() => goTo(current - 1)}
          >
            <ChevronLeft className="size-4" />
          </IconButton>
        </Tooltip>

        <form
          onSubmit={(e) => {
            e.preventDefault()
            goTo(Number(pageInput) || 1)
          }}
          className="flex items-center gap-1.5"
        >
          <label htmlFor="page-jump" className="sr-only">
            Go to page
          </label>
          <Input
            id="page-jump"
            value={pageInput}
            onChange={(e) => setPageInput(e.target.value)}
            inputMode="numeric"
            className="h-6.5 w-14 px-1.5 text-center font-mono text-xs"
          />
          <span className="text-2xs text-ink-3">of {doc.pages}</span>
        </form>

        <Tooltip content="Next page">
          <IconButton
            label="Next page"
            size="xs"
            variant="quiet"
            disabled={current >= doc.pages}
            onClick={() => goTo(current + 1)}
          >
            <ChevronRight className="size-4" />
          </IconButton>
        </Tooltip>

        {span && (
          <Button size="xs" variant="secondary" onClick={() => goTo(span.page)}>
            Back to the cited span · p. {span.page}
          </Button>
        )}

        <div className="ml-auto flex items-center gap-1">
          <IconButton
            label="Zoom out"
            size="xs"
            variant="quiet"
            disabled={zoom <= 0.8}
            onClick={() => setZoom((z) => Math.max(0.8, Number((z - 0.1).toFixed(2))))}
          >
            <Minus className="size-3.5" />
          </IconButton>
          <span className="w-10 text-center font-mono text-[10px] text-ink-3">
            {Math.round(zoom * 100)}%
          </span>
          <IconButton
            label="Zoom in"
            size="xs"
            variant="quiet"
            disabled={zoom >= 1.6}
            onClick={() => setZoom((z) => Math.min(1.6, Number((z + 0.1).toFixed(2))))}
          >
            <Plus className="size-3.5" />
          </IconButton>
        </div>
      </div>

      {doc.ocr && (
        <div className="shrink-0 px-3 pt-3">
          <OcrWarningPanel ocr={doc.ocr} />
        </div>
      )}

      <div ref={scroller} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {/* Spacer for the pages above the window. */}
        <div style={{ height: (first - 1) * (pageHeight + 16) }} aria-hidden />

        <div className="space-y-4">
          {visible.map((n) => (
            <PageSurface
              key={n}
              page={pages.get(n)}
              span={span && span.page === n ? span : null}
              active={!!span && span.page === n}
              zoom={zoom}
              height={pageHeight}
            />
          ))}
        </div>

        {/* Spacer for the pages below. */}
        <div style={{ height: Math.max(0, (doc.pages - last) * (pageHeight + 16)) }} aria-hidden />
      </div>
    </div>
  )
}
